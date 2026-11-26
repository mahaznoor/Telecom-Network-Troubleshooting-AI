"""Administrative review page for response ratings and comments."""

import streamlit as st

from frontend.components import get_feedback_summary
from frontend.components.session_state import is_current_user_admin


_RATING_FILTERS = {
    "All": None,
    "Helpful": "helpful",
    "Not Helpful": "not_helpful",
}


def render_feedback_dashboard() -> None:
    """Render feedback totals and a bounded list of recent submissions."""
    if not is_current_user_admin():
        st.error("Administrator access is required to review feedback.")
        return
    st.title("Feedback review")
    st.caption("Administrative review page")

    selected_filter = st.segmented_control(
        "Filter recent feedback by rating",
        options=list(_RATING_FILTERS),
        default="All",
        key="feedback_dashboard_rating_filter",
    )
    rating_filter = _RATING_FILTERS[selected_filter or "All"]
    result = get_feedback_summary(limit=50, rating=rating_filter)
    if "error" in result:
        st.error(result["error"], title="Feedback unavailable")
        return

    total = result.get("total_feedback", 0)
    helpful = result.get("helpful_count", 0)
    not_helpful = result.get("not_helpful_count", 0)
    positive_percentage = result.get("positive_percentage", 0.0)

    columns = st.columns(4)
    columns[0].metric("Total submissions", total)
    columns[1].metric("Helpful", helpful)
    columns[2].metric("Not helpful", not_helpful)
    columns[3].metric("Positive feedback", f"{positive_percentage:.1f}%")
    st.caption("Summary metrics cover all submissions; the filter applies to the list.")

    if total == 0:
        st.info("No feedback has been submitted yet.")
        return

    recent_feedback = result.get("recent_feedback", [])
    st.subheader("Recent feedback")
    if not recent_feedback:
        st.info("No submissions match this rating filter.")
        return

    for entry in recent_feedback:
        rating = entry.get("rating")
        rating_label = "👍 Helpful" if rating == "helpful" else "👎 Not helpful"
        with st.container(border=True):
            st.markdown(f"**{rating_label}**")
            comment = entry.get("comment")
            st.text(comment if comment else "No comment provided.")
            created_at = entry.get("created_at") or "Timestamp unavailable"
            updated_at = entry.get("updated_at")
            timestamp_text = f"Submitted: {created_at}"
            if updated_at and updated_at != created_at:
                timestamp_text += f" · Updated: {updated_at}"
            st.caption(timestamp_text)
            st.caption(
                f"Session ID: `{entry.get('session_id', 'Unavailable')}` · "
                f"Message ID: `{entry.get('message_id', 'Unavailable')}`"
            )


if __name__ == "__main__":
    render_feedback_dashboard()
