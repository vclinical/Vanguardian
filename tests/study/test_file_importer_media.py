from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, override_settings

from apps.study.infrastructure.repositories.file_importer import StudyFileImporterRepository


class FileImporterMediaTests(SimpleTestCase):
    def test_upload_confirmation_and_runtime_lookup_share_media_root(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            repository = StudyFileImporterRepository()
            upload = repository.create_upload(study_id=3, filename="results.xlsx", content=b"workbook",
                                              preview={}, actor_user_id=7)
            mappings = [{"column": "A", "targets": [{"binding_id": 2, "field_id": 4}]}]
            repository.confirm(study_id=3, upload_id=upload["id"], mappings=mappings, actor_user_id=7)
            destination = Path(directory) / "file_importer" / "3" / upload["id"]
            self.assertEqual((destination / "source.xlsx").read_bytes(), b"workbook")
            self.assertTrue((destination / "upload.json").is_file())
            self.assertTrue((destination / "confirmed.json").is_file())
            reopened = StudyFileImporterRepository()
            self.assertEqual(reopened.list_confirmed_uploads(3)[0]["confirmation"]["mappings"], mappings)
            self.assertTrue(reopened.list_uploads(3)[0]["confirmed"])

    @override_settings(MEDIA_ROOT="/unused-media")
    def test_explicit_repository_root_is_preserved(self):
        self.assertEqual(StudyFileImporterRepository("/custom-root").root, Path("/custom-root"))
