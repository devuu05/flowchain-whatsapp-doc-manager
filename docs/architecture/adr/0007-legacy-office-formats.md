# ADR 0007 — Legacy Office formats

Status: Accepted

- `.xls`: parsed with `xlrd` (maintained, xls-only, no macro execution).
- `.doc` / `.ppt`: no reliable pure-Python parser. Originals are stored and classified from the
  caption/filename only; extraction returns `unsupported` with a warning. Optional conversion
  via headless LibreOffice can be enabled with `LEGACY_CONVERTER=soffice` where the binary is
  installed (not in the default slim image — ~500 MB). Macros are never executed (LibreOffice
  is invoked with macro security at its default "disabled" for headless conversion).
- Macro-enabled OOXML (`.docm/.xlsm/.pptm` or any package containing `vbaProject.bin`) is
  rejected.
