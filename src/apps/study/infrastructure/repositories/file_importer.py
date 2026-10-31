import fcntl
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.study.models import EventDefinition, EventFormBinding


class StudyFileImporterRepository:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else Path(settings.MEDIA_ROOT) / "file_importer"

    def list_bindings(self, study_id):
        return list(EventFormBinding.objects.filter(
            study_id=study_id, deleted=False, is_enabled=True,
            event_definition__study_id=study_id, event_definition__deleted=False,
            event_definition__is_enabled=True, study_version=F("event_definition__study_version"),
            form_definition__study_id=study_id, form_definition__deleted=False, form_definition__is_active=True,
        ).order_by("event_definition__sequence_no", "display_order", "id").values(
            "id", "event_definition_id", "event_definition__code", "event_definition__name",
            "form_definition_id", "form_definition__code", "study_version",
        ))

    def list_events(self, study_id):
        return list(EventDefinition.objects.filter(
            study_id=study_id, deleted=False, is_enabled=True,
        ).order_by("sequence_no", "id").values("id", "code", "name"))

    def _directory(self, study_id, upload_id):
        try:
            identifier = str(UUID(str(upload_id)))
            study_id = int(study_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(_("The file identifier is invalid.")) from exc
        return self.root / str(study_id) / identifier

    @staticmethod
    def _write_json(path, document, *, replace=False):
        # Publish complete JSON atomically; replacement requires the confirmation lock.
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as target:
            temporary = Path(target.name)
            try:
                json.dump(document, target, ensure_ascii=False)
                target.flush()
                os.fsync(target.fileno())
                if replace:
                    os.replace(temporary, path)
                else:
                    os.link(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    def create_upload(self, *, study_id, filename, content, preview, actor_user_id):
        upload_id = str(uuid4())
        directory = self._directory(study_id, upload_id)
        directory.mkdir(parents=True, mode=0o700)
        source = directory / "source.xlsx"
        source.write_bytes(content)
        source.chmod(0o600)
        document = {"id": upload_id, "study_id": study_id, "filename": Path(filename).name[:255],
                    "sha256": hashlib.sha256(content).hexdigest(), "uploaded_by": actor_user_id,
                    "uploaded_at": timezone.now().isoformat(), "preview": preview}
        self._write_json(directory / "upload.json", document)
        return document

    def get_upload(self, study_id, upload_id):
        directory = self._directory(study_id, upload_id)
        try:
            document = json.loads((directory / "upload.json").read_text(encoding="utf-8"))
            confirmation = directory / "confirmed.json"
            document["confirmation"] = json.loads(confirmation.read_text(encoding="utf-8")) if confirmation.exists() else None
            return document
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise ValueError(_("The file was not found in this study or has not finished saving.")) from exc

    def confirm(self, *, study_id, upload_id, mappings, actor_user_id, expected_confirmed_at=None):
        directory = self._directory(study_id, upload_id)
        self.get_upload(study_id, upload_id)
        # Serialize the version check and atomic publish across workers on shared storage.
        with (directory / "confirmation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = self.get_upload(study_id, upload_id)["confirmation"]
            if expected_confirmed_at is None:
                if current is not None:
                    raise ValueError("Mapping này đã được xác nhận. Mở lại mapping và chọn Chỉnh sửa mapping.")
            elif current is None or current["confirmed_at"] != expected_confirmed_at:
                raise ValueError("Mapping đã thay đổi. Mở lại mapping mới nhất trước khi chỉnh sửa.")
            record = {"mappings": mappings, "confirmed_by": actor_user_id, "confirmed_at": timezone.now().isoformat()}
            if current is not None:
                revisions = directory / "revisions"
                revisions.mkdir(mode=0o700, exist_ok=True)
                self._write_json(revisions / f"{uuid4()}.json", current)
            self._write_json(directory / "confirmed.json", record, replace=current is not None)
        return record

    def list_uploads(self, study_id):
        directory = self.root / str(int(study_id))
        if not directory.exists():
            return []
        records = []
        for path in sorted(directory.glob("*/upload.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
            try:
                record = self.get_upload(study_id, path.parent.name)
            except ValueError:
                continue
            records.append({key: record[key] for key in ("id", "filename", "uploaded_at")}
                           | {"confirmed": record["confirmation"] is not None})
        return records

    def list_confirmed_uploads(self, study_id):
        """Runtime lookup must include old mappings outside the UI history window."""
        directory = self.root / str(int(study_id))
        records = []
        for path in sorted(directory.glob("*/confirmed.json")):
            try:
                record = self.get_upload(study_id, path.parent.name)
            except ValueError:
                continue
            if record.get("confirmation"):
                records.append(record)
        return records
