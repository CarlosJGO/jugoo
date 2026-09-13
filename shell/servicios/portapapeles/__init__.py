"""Clipboard history for the Jugoo pickers. Never sent to the network or Llama."""

from .historia import (
	ClipboardEntry,
	ClipboardHistory,
	find_match_spans,
	format_copied_ago,
	preview_match,
	preview_text,
	search_entries,
)
from .servicio import (
	CLIPBOARD_CHANGED,
	ClipboardService,
	copy_image_bytes,
	copy_text,
	paste_text,
	paste_text_to_window,
)
