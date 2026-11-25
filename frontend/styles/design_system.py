"""Design system CSS loader for Streamlit."""

import os


def _load_css() -> str:
    """Load the design system CSS file."""
    css_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "styles",
        "design_system.css"
    )
    try:
        with open(css_path, "r", encoding="utf-8") as f:
            return f"<style>\n{f.read()}\n</style>"
    except FileNotFoundError:
        return ""


CSS = _load_css()