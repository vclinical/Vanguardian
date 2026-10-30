from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.study.models import EventDefinition, Site, Study, SubjectEligibilityAssessment
from apps.subject.application.services.workflow_action import SubjectWorkflowActionResult, SubjectWorkflowActionService
from apps.subject.infrastructure.repositories.workflow_action import DjangoSubjectWorkflowActionRepository
from apps.subject.models import Subject, SubjectEnrollment, SubjectEventInstance, SubjectRandomization
from apps.subject.presentation.web.views.trigger_workflow import SubjectTriggerWorkflowView


class FailedEligibilityWorkflowAccessTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        timestamps = {"created_at": self.now, "updated_at": self.now}
        self.study = Study.objects.create(code="RECOVERY", name="Recovery", **timestamps)
        self.site = Site.objects.create(code="SITE", name="Site", study=self.study, **timestamps)
        self.subject = Subject.objects.create(
            screening_code="RECOVERY-S039", current_sequence=39, study=self.study, site=self.site, **timestamps
        )
        self.definition = EventDefinition.objects.create(
            study=self.study,
            study_version="v1.0",
            code="ELIGIBILITY_ASSESSMENT",
            name="Eligibility assessment",
            event_type="operational",
            event_category="screening",
            execution_mode="workflow_action",
            **timestamps,
        )
        self.event = SubjectEventInstance.objects.create(
            study=self.study,
            subject=self.subject,
            event_definition=self.definition,
            study_version="v1.0",
            status="completed",
            **timestamps,
        )
        self.assessment = SubjectEligibilityAssessment.objects.create(
            study=self.study,
            site=self.site,
            subject_id=self.subject.pk,
            event_instance_id=self.event.pk,
            assessment_type="SCREENING",
            assessment_no=1,
            result="NOT_ELIGIBLE",
            assessment_status="FINAL",
            is_current=True,
            source_context="datacapture",
            source_object_type="EVENT_INSTANCE",
            source_object_id=1357,
            study_version="v1.0",
            **timestamps,
        )
        self.repository = DjangoSubjectWorkflowActionRepository()
        self.scope = {
            "study_id": self.study.pk,
            "subject_id": self.subject.pk,
            "event_instance_id": self.event.pk,
        }

    def assert_not_exposed(self):
        self.assertFalse(self.repository.is_open_workflow_action_event(**self.scope))
        self.assertIsNone(self.repository.get_open_workflow_action_context(**self.scope))
        self.assertEqual(
            self.repository.map_open_workflow_action_event_id_by_subject_id(
                study_id=self.study.pk, subject_ids=[self.subject.pk]
            ),
            {},
        )
        self.assertEqual(
            self.repository.map_open_workflow_action_context_by_subject_id(
                study_id=self.study.pk, subject_ids=[self.subject.pk]
            ),
            {},
        )

    def test_completed_current_failure_is_exposed_consistently_to_ui_and_post(self):
        self.assertTrue(self.repository.is_open_workflow_action_event(**self.scope))
        context = self.repository.get_open_workflow_action_context(**self.scope)
        self.assertEqual(context.event_instance_id, self.event.pk)
        self.assertEqual(context.status, "completed")
        self.assertEqual(
            self.repository.map_open_workflow_action_event_id_by_subject_id(
                study_id=self.study.pk, subject_ids=[self.subject.pk]
            ),
            {self.subject.pk: self.event.pk},
        )
        access = SubjectWorkflowActionService(repository=self.repository).map_triggerable_event_access_by_subject_id(
            study_id=self.study.pk, subject_ids=[self.subject.pk]
        )
        self.assertEqual(access[self.subject.pk].permission_code, "study.finalize_subject_eligibility")

    def test_pass_or_inactive_assessment_is_not_offered_as_recovery(self):
        for changes in (
            {"result": "ELIGIBLE"},
            {"is_current": False},
            {"assessment_status": "RETRACTED"},
            {"deleted": True},
            {"assessment_type": "OTHER"},
        ):
            with self.subTest(changes=changes):
                SubjectEligibilityAssessment.objects.filter(pk=self.assessment.pk).update(**changes)
                self.assert_not_exposed()
                SubjectEligibilityAssessment.objects.filter(pk=self.assessment.pk).update(
                    result="NOT_ELIGIBLE", is_current=True, assessment_status="FINAL", deleted=False,
                    assessment_type="SCREENING",
                )

    def test_enrolled_subject_is_not_offered_as_recovery(self):
        SubjectEnrollment.objects.create(
            study=self.study, site=self.site, subject=self.subject, status="Enrolled", is_enrolled=True,
            created_at=self.now, updated_at=self.now,
        )

        self.assert_not_exposed()

    def test_randomized_subject_is_not_offered_as_recovery(self):
        SubjectRandomization.objects.create(
            study=self.study, site=self.site, subject=self.subject, randomization_status="randomized",
            randomization_number="R039",
            created_at=self.now, updated_at=self.now,
        )

        self.assert_not_exposed()

    def test_completed_noneligibility_event_is_not_offered_as_recovery(self):
        self.definition.code = "ENROLLMENT"
        self.definition.save(update_fields=["code"])

        self.assert_not_exposed()

    def test_recovery_lookup_is_scoped_to_subject_and_study(self):
        self.assertFalse(
            self.repository.is_open_workflow_action_event(**{**self.scope, "subject_id": self.subject.pk + 100})
        )
        self.assertIsNone(
            self.repository.get_open_workflow_action_context(**{**self.scope, "study_id": self.study.pk + 100})
        )

    def test_completed_failed_workflow_view_uses_finalize_permission_and_runs_service(self):
        service = SubjectWorkflowActionService(repository=self.repository)
        service.execute_for_open_event = Mock(
            return_value=SubjectWorkflowActionResult(
                event_instance_id=self.event.pk, executed=True, action="eligibility_assessment"
            )
        )
        view = SubjectTriggerWorkflowView()
        view.kwargs = self.scope
        view.service_class = lambda: service
        self.assertEqual(view.get_permission_required(), ("study.finalize_subject_eligibility",))
        request = RequestFactory().post("/trigger-workflow/")
        request.user = SimpleNamespace(pk=99)

        with patch("apps.subject.presentation.web.views.trigger_workflow.messages") as messages:
            response = view.post(request, **self.scope)

        self.assertEqual(response.status_code, 302)
        service.execute_for_open_event.assert_called_once_with(event_instance_id=self.event.pk, actor_user_id=99)
        messages.success.assert_called_once()
