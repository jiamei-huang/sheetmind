"""Shared application objects for route handlers."""

from sheetmind.analysis import SheetMindAgent, get_artifact_store, get_context_store
from sheetmind.config import settings
from sheetmind.services.conversations import ConversationService
from sheetmind.services.excel import ExcelService
from sheetmind.services.anonymous_sessions import AnonymousSessionService
from sheetmind.services.projects import ProjectService
from sheetmind.services.tasks import TaskService
from sheetmind.services.uploads import FileUploader


projects = ProjectService(settings.claim_legacy_projects)
anonymous_sessions = AnonymousSessionService(settings.anonymous_session_ttl_days)
tasks = TaskService()
excel = ExcelService()
uploads = FileUploader()
conversations = ConversationService()
analysis_agent = SheetMindAgent()
analysis_contexts = get_context_store()
analysis_artifacts = get_artifact_store()
