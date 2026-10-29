from django.urls import include, path

from apps.study.presentation.web.views import (
    SiteCreateView,
    SiteDeleteView,
    SiteDetailView,
    SiteListView,
    SiteMembershipOptionsApiView,
    StudyCreateView,
    StudyCrfPageLifecycleConfigView,
    StudyCrfSectionLayoutConfigImportTemplateView,
    StudyCrfTemplateFieldImportTemplateView,
    StudyCrfTemplateImportTemplateView,
    StudyCrfTemplateListView,
    StudyCrfValidationRuleImportTemplateView,
    StudyDeleteView,
    StudyDetailView,
    StudyEventAttestationPolicyImportTemplateView,
    StudyEventDefinitionCreateView,
    StudyEventDefinitionImportTemplateView,
    StudyEventDefinitionListView,
    StudyEventFormBindingImportTemplateView,
    StudyEventFormDisplayLabelConfigView,
    StudyFactMappingImportTemplateView,
    StudyListView,
    StudyManageRolesView,
    StudyNng31MasterListApprovalView,
    StudyNng31MasterListImportCommitView,
    StudyNng31MasterListImportPreviewView,
    StudyRandomizationArmDeleteView,
    StudyRandomizationArmImportCommitView,
    StudyRandomizationArmImportPreviewView,
    StudyRandomizationSchemeDeleteView,
    StudyRandomizationSchemeImportCommitView,
    StudyRandomizationSchemeImportPreviewView,
    StudyRandomizationSequencePeriodImportCommitView,
    StudyRandomizationSequencePeriodImportPreviewView,
    StudyRandomizationView,
    StudyRoleCreateView,
    StudySubjectIdentifierPolicyPreviewView,
    StudySubjectIdentifierPolicyRollbackPreviewView,
    StudySubjectIdentifierPolicyRollbackView,
    StudyToggleStatusView,
    StudyUpdateView,
)
from apps.study.presentation.web.views.file_importer import StudyFileImporterView

app_name = "study"


urlpatterns = [
    path("studies/<int:study_id>/file-importer", StudyFileImporterView.as_view(), name="study_file_importer"),
    path("studies", StudyListView.as_view(), name="study_list"),
    path("studies/new", StudyCreateView.as_view(), name="study_create"),
    path("studies/<int:study_id>", StudyDetailView.as_view(), name="study_detail"),
    path(
        "studies/<int:study_id>/crf-page-lifecycle",
        StudyCrfPageLifecycleConfigView.as_view(),
        name="study_crf_page_lifecycle",
    ),
    path("studies/<int:study_id>/roles", StudyManageRolesView.as_view(), name="study_manage_roles"),
    path("studies/<int:study_id>/roles/create", StudyRoleCreateView.as_view(), name="study_role_create"),
    path("studies/<int:study_id>/crftemplates/import", StudyCrfTemplateImportTemplateView.as_view(), name="study_crf_template_import"),
    path("studies/<int:study_id>/crftemplates/fields/import", StudyCrfTemplateFieldImportTemplateView.as_view(), name="study_crf_template_field_import"),
    path("studies/<int:study_id>/crftemplates/section-layout-configs/import", StudyCrfSectionLayoutConfigImportTemplateView.as_view(), name="study_crf_section_layout_config_import"),
    path("studies/<int:study_id>/crftemplates/validation-rules/import", StudyCrfValidationRuleImportTemplateView.as_view(), name="study_crf_validation_rule_import"),
    path("studies/<int:study_id>/crftemplates/attestation-policies/import", StudyEventAttestationPolicyImportTemplateView.as_view(), name="study_event_attestation_policy_import"),
    path("studies/<int:study_id>/crftemplates", StudyCrfTemplateListView.as_view(), name="study_crf_templates"),
    path("studies/<int:study_id>/eventdefinitions/bindingforms/import", StudyEventFormBindingImportTemplateView.as_view(), name="study_event_form_binding_import"),
    path("studies/<int:study_id>/eventdefinitions/display-labels", StudyEventFormDisplayLabelConfigView.as_view(), name="study_event_form_display_labels"),
    path("studies/<int:study_id>/eventdefinitions/factmappings/import", StudyFactMappingImportTemplateView.as_view(), name="study_fact_mapping_import"),
    path("studies/<int:study_id>/eventdefinitions/import", StudyEventDefinitionImportTemplateView.as_view(), name="study_event_definition_import"),
    path("studies/<int:study_id>/eventdefinitions/new", StudyEventDefinitionCreateView.as_view(), name="study_event_definition_create"),
    path("studies/<int:study_id>/eventdefinitions", StudyEventDefinitionListView.as_view(), name="study_event_definitions"),
    path("studies/<int:study_id>/randomization", StudyRandomizationView.as_view(), name="study_randomization"),
    path("studies/<int:study_id>/delete", StudyDeleteView.as_view(), name="study_delete"),
    path("studies/<int:study_id>/edit", StudyUpdateView.as_view(), name="study_update"),
    path(
        "studies/<int:study_id>/subject-identifier-policy/preview",
        StudySubjectIdentifierPolicyPreviewView.as_view(),
        name="study_subject_identifier_policy_preview",
    ),
    path(
        "studies/<int:study_id>/subject-identifier-policy/rollback/preview",
        StudySubjectIdentifierPolicyRollbackPreviewView.as_view(),
        name="study_subject_identifier_policy_rollback_preview",
    ),
    path(
        "studies/<int:study_id>/subject-identifier-policy/rollback",
        StudySubjectIdentifierPolicyRollbackView.as_view(),
        name="study_subject_identifier_policy_rollback",
    ),
    path(
        "studies/<int:study_id>/toggle-status", StudyToggleStatusView.as_view(),
        name="study_toggle_status",
    ),
]

# Study.Randomization
urlpatterns += [
    path(
        "studies/<int:study_id>/randomization/", include(
            [
                path(
                    "schemes/import/preview",
                    StudyRandomizationSchemeImportPreviewView.as_view(),
                    name="study_randomization_scheme_import_preview",
                ),
                path(
                    "schemes/import/commit",
                    StudyRandomizationSchemeImportCommitView.as_view(),
                    name="study_randomization_scheme_import_commit",
                ),
                path(
                    "schemes/<int:scheme_id>/delete",
                    StudyRandomizationSchemeDeleteView.as_view(),
                    name="study_randomization_scheme_delete",
                ),
                path(
                    "arms/import/preview",
                    StudyRandomizationArmImportPreviewView.as_view(),
                    name="study_randomization_arm_import_preview",
                ),
                path(
                    "arms/import/commit",
                    StudyRandomizationArmImportCommitView.as_view(),
                    name="study_randomization_arm_import_commit",
                ),
                path(
                    "arms/<int:arm_id>/delete",
                    StudyRandomizationArmDeleteView.as_view(),
                    name="study_randomization_arm_delete",
                ),
                path(
                    "sequence-periods/import/preview",
                    StudyRandomizationSequencePeriodImportPreviewView.as_view(),
                    name="study_randomization_sequence_period_import_preview",
                ),
                path(
                    "sequence-periods/import/commit",
                    StudyRandomizationSequencePeriodImportCommitView.as_view(),
                    name="study_randomization_sequence_period_import_commit",
                ),
                path(
                    "master-list/import/preview",
                    StudyNng31MasterListImportPreviewView.as_view(),
                    name="study_nng31_master_list_import_preview",
                ),
                path(
                    "master-list/import/commit",
                    StudyNng31MasterListImportCommitView.as_view(),
                    name="study_nng31_master_list_import_commit",
                ),
                path(
                    "schemes/<int:scheme_id>/master-list/approve",
                    StudyNng31MasterListApprovalView.as_view(),
                    name="study_nng31_master_list_approve",
                ),
            ],
        ),
    ),
]


# Study.Site
urlpatterns += [
    path(
        "api/studies/<int:study_id>/sites/<int:site_id>/memberships",
        SiteMembershipOptionsApiView.as_view(),
        name="api_site_memberships",
    ),
    path(
        "studies/<int:study_id>/sites/", include(
            [
                path("", SiteListView.as_view(), name="site_list"),
                path("new", SiteCreateView.as_view(), name="site_create"),
                path("<int:site_id>", SiteDetailView.as_view(), name="site_detail"),
                path("<int:site_id>/delete", SiteDeleteView.as_view(), name="site_delete"),
            ],
        ),
    ),
]
