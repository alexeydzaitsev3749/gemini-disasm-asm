# gemini-disasm-asm

[Читать на русском (README.ru.md)](README.ru.md)

A zero-dependency, K&R-styled disassembler/linker pair for **Google AI Studio** session logs.

Implements non-destructive, two-pass binary stripping and relinking for prompt contexts (`chunkedPrompt.chunks`). Safely excises heavy attachments (Drive documents, images, video) and latent cryptographic state (`thoughtSignature`), while maintaining virtual index relocation tables (`gemini-stash/2`) and OS file descriptor / Google Drive sync bindings (`r+` in-place).

---

## The Field-Strip Cure (The "AKM & D-6 Engine" Principle)

When working on complex, long-running sessions in Google AI Studio, a prompt can suddenly enter a terminal failure state: **every query triggers HTTP 500 "Internal Error"**, even when no broken external links (such as deleted YouTube videos) exist.

Often, this is caused by server-side state corruption, attention cache deadlocks, or oversized latent signatures.

Like field-stripping a stubborn mechanical engine, the cure is simple:
1. **Disassemble:** Strip all attachments and signatures into an external stash.
2. **Clear the Chamber:** Submit a minimal prompt (e.g., "summarize") to force Google's backend to reset its session cache.
3. **Assemble:** Relink the stripped components back into the active prompt incrementally.

---

## How It Works

### 1. `gemini_disasm.py` (The Stripper)
* Analyzes `chunkedPrompt.chunks` and tracks virtual indices (`v`) to ensure stable addressing even after chunks are removed.
* Extracts media blobs and `thoughtSignature` records into a sidecar `<log>_stash.json` (`gemini-stash/2` format).
* Stores a cryptographic integrity skeleton (`[role, createTime]`) to prevent out-of-order reassembly if the log was tampered with.
* Modifies the primary log strictly in-place (`r+`, `seek(0)`, `truncate()`), preserving the filesystem inode and cloud `googleId`.

### 2. `gemini_asm.py` (The Linker)
* Performs two-way validation against the skeleton.
* Restores signatures at exact dictionary positions (`restore_sig`).
* Re-inserts attachments in monotonic virtual index order.
* Supports staged, selective relinking (`--with signatures`, `--with video`, `--pick N`, `--ids ID`).
* Automatically deletes the stash file once all components have been reintegrated.

---

## Usage

### Phase 1: Inspection & Disassembly
```bash
# 1. Inventory heavy resources without modifying the file:
python gemini_disasm.py chat.json

# 2. Strip everything into chat_stash.json:
python gemini_disasm.py chat.json --only all

# Or strip selectively:
python gemini_disasm.py chat.json --only video signatures
