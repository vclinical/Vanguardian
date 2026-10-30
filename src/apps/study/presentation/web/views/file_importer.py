from apps.study.presentation.web.views.studies import StudyDetailView


class StudyFileImporterView(StudyDetailView):
    permission_required = "STUDY_CONFIG.MANAGE"
    template_name = "study/file_importer.html"
    http_method_names = ["get", "head", "options"]
