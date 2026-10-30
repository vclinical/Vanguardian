"""Public read boundary for confirmed file-import configurations."""
from django.utils.translation import gettext as _

from apps.study.application.services.file_importer import StudyFileImporterService
from apps.study.infrastructure.repositories.file_importer import StudyFileImporterRepository


class StudyFileImportReader:
    def __init__(self, repository=None):
        self.repository = repository or StudyFileImporterRepository()

    def configurations(self, study_id):
        return self.repository.list_confirmed_uploads(study_id)

    def configuration(self, study_id, mapping_id):
        record = self.repository.get_upload(study_id, mapping_id)
        if not record.get("confirmation"):
            raise ValueError(_("The mapping has not been confirmed."))
        return record

    def catalog(self, study_id):
        return StudyFileImporterService(repository=self.repository).catalog(study_id)
