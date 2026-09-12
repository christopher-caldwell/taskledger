import re

_control = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)?)")


def safe_text(value: object) -> str:
    return _control.sub("", str(value)).replace("[", "\\[")
