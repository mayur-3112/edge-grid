"""HTTP client layer for the Edge Grid: OpenAI-compatible gateway + operator dashboard.

The gateway is the only component a developer has to know about. It speaks the
OpenAI chat-completions wire format, and behind that endpoint it runs the real
Edge Grid pipeline: signed job request -> second-price auction -> streaming
inference -> DA commitment -> sampled verification -> settlement.
"""

__version__ = "0.1.0"
