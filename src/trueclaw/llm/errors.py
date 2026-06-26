from __future__ import annotations


class LLMError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, body: object = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def map_http_error(status_code: int, body: object) -> LLMError:
    if status_code == 401:
        return LLMError("LLM unauthorized (check apiKey)", status_code=status_code, body=body)
    if status_code == 429:
        return LLMError("LLM rate limited", status_code=status_code, body=body)
    if status_code >= 500:
        return LLMError("LLM upstream error", status_code=status_code, body=body)
    return LLMError(f"LLM request failed status={status_code}", status_code=status_code, body=body)
