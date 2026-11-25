"""Telecom Intelligence AI - Main Streamlit Application.

Professional enterprise dashboard for telecom network troubleshooting
inspired by Huawei NOC interfaces.
"""

import streamlit as st
from frontend.components import init_app_session_state, render_sidebar, get_current_page
from frontend.pages import PAGE_RENDERERS
from frontend.styles.design_system import CSS as DESIGN_SYSTEM_CSS


def load_design_system():
    """Load the design system CSS."""
    st.markdown(DESIGN_SYSTEM_CSS, unsafe_allow_html=True)


def main():
    """Main application entry point."""
    # Page configuration
    st.set_page_config(
        page_title="Telecom Intelligence AI",
        page_icon="📡",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            'Get Help': None,
            'Report a bug': None,
            'About': "Telecom Intelligence AI v1.0\nNetwork Operations Platform\nPowered by Groq & LangChain"
        }
    )
    
    # Load design system
    load_design_system()
    
    # Initialize session state
    init_app_session_state()
    
    # Render sidebar (handles navigation)
    render_sidebar()
    
    # Get current page and render
    current_page = get_current_page()
    renderer = PAGE_RENDERERS.get(current_page)
    
    if renderer:
        renderer()
    else:
        # Fallback to overview
        PAGE_RENDERERS["overview"]()


if __name__ == "__main__":
    main()