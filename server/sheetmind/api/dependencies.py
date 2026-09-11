"""Shared application objects for route handlers."""

from sheetmind.analysis import SheetMindAgent, get_context_store
from sheetmind.services.conversations import ConversationService
from sheetmind.services.excel import ExcelService
from sheetmind.services.anonymous_sessions import AnonymousSessionService
from sheetmind.services.projects import ProjectService
from sheetmind.services.tasks import TaskService
from sheetmind.services.uploads import FileUploader


projects = ProjectService()
anonymous_sessions = AnonymousSessionService()
tasks = TaskService()
excel = ExcelService()
uploads = FileUploader()
conversations = ConversationService()
analysis_agent = SheetMindAgent()
analysis_contexts = get_context_store()
