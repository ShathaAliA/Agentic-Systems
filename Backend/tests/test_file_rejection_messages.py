"""PR 4 — File rejection messages (safe, specific, no str(e) in code)."""
import inspect


def test_rejection_messages_are_safe():
    """Router must NOT leak str(e) or DB traces in executable code (comments excluded)."""
    from app.documents import router as router_module
    source = inspect.getsource(router_module)

    # نستثني التعليقات (أسطر تبدأ بـ #) لأنها شرح وليست كوداً
    code_lines = [
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)

    assert "str(e)" not in code, "Router must not use str(e) in code (comments OK)"
    assert "str(exception)" not in code, "Router must not use str(exception)"


def test_rejection_message_dict_exists():
    """Router must map internal reasons to safe Arabic messages."""
    from app.documents.router import REJECTION_MESSAGES

    required = {
        "unsupported_file_type",
        "extension_mismatch",
        "password_required",
        "too_many_pages",
        "invalid_format",
    }
    assert required.issubset(REJECTION_MESSAGES.keys())

    for key, msg in REJECTION_MESSAGES.items():
        assert isinstance(msg, str) and len(msg) > 5, f"Empty message for {key}"


def test_file_processor_has_page_limit():
    """file_processor must enforce 100-page limit for PDF."""
    from app.documents import file_processor
    source = inspect.getsource(file_processor)

    assert "page_count > 100" in source or "page_count>100" in source.replace(" ", "")
    assert "too_many_pages" in source


def test_file_processor_has_encryption_check():
    """file_processor must reject password-protected PDFs."""
    from app.documents import file_processor
    source = inspect.getsource(file_processor)

    assert "needs_pass" in source
    assert "password_required" in source