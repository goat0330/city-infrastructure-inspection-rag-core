from .parser import LabelParseError, parse_label_docx


def build_gold(*args, **kwargs):
    from .builder import build_gold as _build_gold

    return _build_gold(*args, **kwargs)


__all__ = ["LabelParseError", "build_gold", "parse_label_docx"]
