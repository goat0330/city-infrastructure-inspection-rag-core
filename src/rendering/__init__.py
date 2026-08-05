"""DOCX rendering for structured inspection reports."""

from .docx_renderer import render_report
from .submission_document import SubmissionDocument, build_submission_document
from .template_renderer import render_template_report

__all__ = [
    "SubmissionDocument",
    "build_submission_document",
    "render_report",
    "render_template_report",
]
