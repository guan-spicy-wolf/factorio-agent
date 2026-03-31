"""Script review manager.

Current implementation: auto-approve all scripts.
Future: integrate with GitHub PR + LLM review.
"""

from typing import Optional


class ReviewManager:
    """Manages script review workflow.

    Current: auto-approve everything.
    Future: create PR, trigger LLM review, merge on approval.
    """

    def submit(self, name: str, code: str, author: str = "agent") -> dict:
        """Submit a script for review.

        Args:
            name: Script name (e.g. "atomic.my_action")
            code: Lua source code
            author: Who submitted (default: "agent")

        Returns:
            Current: {"status": "approved", ...}
            Future: {"status": "pending_review", "pr_url": ...}
        """
        # TODO: Future implementation
        # - Create git branch
        # - Write file
        # - Create PR
        # - Trigger LLM review
        return {
            "status": "approved",
            "name": name,
            "reviewer": "auto-pass",
            "author": author,
        }

    def check_status(self, name: str) -> dict:
        """Check review status for a script.

        Returns:
            Current: always approved
            Future: query PR status
        """
        # TODO: Query PR status
        return {
            "status": "approved",
            "name": name,
        }


# Global instance
_review_manager: Optional[ReviewManager] = None


def get_review_manager() -> ReviewManager:
    """Get the global review manager instance."""
    global _review_manager
    if _review_manager is None:
        _review_manager = ReviewManager()
    return _review_manager