"""Frontend pages package."""

from frontend.pages.overview import render_overview_page
from frontend.pages.ai_assistant import render_ai_assistant_page
from frontend.pages.knowledge_base import render_knowledge_base_page
from frontend.pages.network_docs import render_network_docs_page
from frontend.pages.system_health import render_system_health_page
from frontend.pages.chat_history import render_chat_history_page
from frontend.pages.settings import render_settings_page
from frontend.pages.feedback_dashboard import render_feedback_dashboard
from frontend.pages.authentication import render_authentication_page
from frontend.pages.password_reset import render_password_reset_page

PAGE_RENDERERS = {
    "authentication": render_authentication_page,
    "password_reset": render_password_reset_page,
    "overview": render_overview_page,
    "ai_assistant": render_ai_assistant_page,
    "knowledge_base": render_knowledge_base_page,
    "network_docs": render_network_docs_page,
    "system_health": render_system_health_page,
    "chat_history": render_chat_history_page,
    "feedback_dashboard": render_feedback_dashboard,
    "settings": render_settings_page,
}

__all__ = [
    "render_overview_page",
    "render_ai_assistant_page",
    "render_knowledge_base_page",
    "render_network_docs_page",
    "render_system_health_page",
    "render_chat_history_page",
    "render_feedback_dashboard",
    "render_authentication_page",
    "render_password_reset_page",
    "render_settings_page",
    "PAGE_RENDERERS",
]