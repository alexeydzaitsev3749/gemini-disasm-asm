#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gemini_disasm.py  --  дизассемблер лога Gemini (AI Studio), работает IN-PLACE.

Исходный файл открывается в r+ и перезаписывается на месте (тот же inode / тот же
Google Drive file-id, чтобы веб-Gemini подтянула правку). В логе остаются хедер,
диалог и трейлер; вырезанное (вложения, thoughtSignature) уходит в <лог>_stash.json
вместе с координатами для точной обратной вклейки (gemini_asm.py).

Можно резать в несколько заходов (разными ключами): stash дополняется.
Чат можно продолжать (новые сообщения в конец) -- координаты не ломаются.

Запуск БЕЗ --only ничего не меняет, только показывает, что можно вырезать.

Ключи (--only):
    signatures
    driveImage driveDocument driveVideo youtubeVideo inlineImage inlineFile
    алиасы: attachments | video (driveVideo+youtubeVideo) | drive (driveImage+driveDocument+driveVideo) | all

Примеры:
    python gemini_disasm.py chat.json                          # только инвентаризация
    python gemini_disasm.py chat.json --only all
    python gemini_disasm.py chat.json --only video signatures
    python gemini_disasm.py chat.json --only inlineImage inlineFile --backup
"""
import argparse, copy, json, os, shutil, sys

ATTACH_KEYS = ("driveImage", "driveDocument", "driveVideo", "youtubeVideo", "inlineImage", "inlineFile")
SIG_KEYS = ("thoughtSignature", "thought_signature")
ALIASES = {
    "attachments": set(ATTACH_KEYS),
    "video": {"driveVideo", "youtubeVideo"},
    "drive": {"driveImage", "driveDocument", "driveVideo"},
    "signatures": {"signatures"},
}
FORMAT = "gemini-stash/2"


def expand_kinds(names):
    out = set()
    for n in names:
        if n == "all":
            out |= set(ATTACH_KEYS) | {"signatures"}
        elif n in ALIASES:
            out |= ALIASES[n]
        elif n in ATTACH_KEYS:
            out.add(n)
        else:
            sys.exit(f"[!] неизвестный ключ '{n}'")
    return out


def att_id(chunk, kind):
    v = chunk.get(kind)
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return v[:60]
    if isinstance(v, dict):
        return str(v.get("id") or v.get("uri") or v.get("fileUri") or v.get("url") or "")
    return ""


def find_sigs(obj, path=()):
    found = []
    if isinstance(obj, dict):
        for pos, k in enumerate(list(obj.keys())):
            if k in SIG_KEYS:
                found.append((path, obj, k, pos))
            else:
                found.extend(find_sigs(obj[k], path + (k,)))
    elif isinstance(obj, list):
        for i, it in enumerate(obj):
            found.extend(find_sigs(it, path + (i,)))
    return found


def virtual_indices(n_present, absent):
    """Виртуальный (исходный) индекс каждого присутствующего в логе чанка."""
    out, v = [], 0
    while len(out) < n_present:
        if v not in absent:
            out.append(v)
        v += 1
    return out


def sk(c):
    return [c.get("role"), c.get("createTime")]


def verify(chunks, vs, skeleton):
    for p, v in enumerate(vs):
        if v < len(skeleton) and sk(chunks[p]) != skeleton[v]:
            return p
    return None


def main():
    ap = argparse.ArgumentParser(description="Дизассемблер лога Gemini (in-place).")
    ap.add_argument("log")
    ap.add_argument("--only", nargs="+", metavar="KEY", help="что вырезать; без этого флага -- только инвентаризация")
    ap.add_argument("--stash", help="путь к stash (по умолчанию <лог>_stash.json рядом с логом)")
    ap.add_argument("--backup", action="store_true", help="перед правкой сохранить <лог>.bak")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="не сверять лог со скелетом stash")
    a = ap.parse_args()

    base, ext = os.path.splitext(a.log)
    stash_path = a.stash or f"{base}_stash.json"

    with open(a.log, "r+", encoding="utf-8") as f:
        data = json.load(f)
        try:
            chunks = data["chunkedPrompt"]["chunks"]
        except KeyError:
            sys.exit("[!] нет chunkedPrompt.chunks")

        if os.path.exists(stash_path):
            with open(stash_path, encoding="utf-8") as sf:
                stash = json.load(sf)
            if stash.get("format") != FORMAT:
                sys.exit("[!] неизвестный формат stash")
            print(f"[*] найден stash: вложений {len(stash['attachments'])}, подписей {len(stash['signatures'])}")
        else:
            stash = {"format": FORMAT, "source": os.path.basename(a.log),
                     "skeleton": [], "attachments": [], "signatures": []}

        absent = {it["v"] for it in stash["attachments"]}
        vs = virtual_indices(len(chunks), absent)
        bad = verify(chunks, vs, stash["skeleton"])
        if bad is not None and not a.force:
            sys.exit(f"[!] лог не соответствует stash (чанк #{bad} другой роли/времени): его правили? --force чтобы обойти")
        for p, v in enumerate(vs):                      # скелет растёт вместе с чатом
            if v >= len(stash["skeleton"]):
                stash["skeleton"].append(sk(chunks[p]))

        # --- инвентаризация
        inv, inv_tok, sig_n, sig_chars = {}, {}, 0, 0
        for c in chunks:
            k = next((k for k in ATTACH_KEYS if k in c), None)
            if k:
                inv[k] = inv.get(k, 0) + 1
                inv_tok[k] = inv_tok.get(k, 0) + (c.get("tokenCount") or 0)
            else:
                for _, parent, key, _ in find_sigs(c):
                    sig_n += 1
                    sig_chars += len(parent[key]) if isinstance(parent[key], str) else 0
        if not a.only:
            print(f"[*] В логе сейчас чанков: {len(chunks)}")
            for k in ATTACH_KEYS:
                if k in inv:
                    print(f"    {k:<14} {inv[k]:>4} шт.  {inv_tok[k]:>8} токенов")
            print(f"    {'signatures':<14} {sig_n:>4} шт.  {sig_chars/1024:>8.1f} KB")
            print("[*] Файл НЕ изменён. Укажи --only KEY ... (или --only all) чтобы вырезать.")
            return

        kinds = expand_kinds(a.only)
        new_chunks, n_att, n_sig = [], 0, 0
        for p, c in enumerate(chunks):
            v = vs[p]
            kind = next((k for k in ATTACH_KEYS if k in c), None)
            if kind and kind in kinds:
                stash["attachments"].append({"v": v, "kind": kind, "id": att_id(c, kind),
                                             "tokenCount": c.get("tokenCount"), "chunk": c})
                n_att += 1
                continue
            if "signatures" in kinds and not kind:
                c = copy.deepcopy(c)
                for path, parent, key, pos in find_sigs(c):
                    stash["signatures"].append({"v": v, "path": list(path), "key": key,
                                                "pos": pos, "value": parent[key]})
                    n_sig += 1
                for path, parent, key, pos in find_sigs(c):
                    parent.pop(key, None)
            new_chunks.append(c)

        if not n_att and not n_sig:
            print("[*] Нечего вырезать. Файл не изменён.")
            return
        stash["attachments"].sort(key=lambda it: it["v"])
        stash["signatures"].sort(key=lambda it: it["v"])      # sort стабилен: порядок внутри чанка сохранён

        print(f"[+] вырезано: вложений {n_att}, подписей {n_sig}; чанков {len(chunks)} -> {len(new_chunks)}")
        if a.dry_run:
            print("[*] --dry-run: ничего не записано.")
            return

        if a.backup:
            shutil.copy2(a.log, a.log + ".bak")
            print(f"[+] бэкап: {a.log}.bak")
        # 1) сначала stash (как и в остальных утилитах: сначала копия, потом правка оригинала)
        with open(stash_path, "w", encoding="utf-8") as sf:
            json.dump(stash, sf, indent=2, ensure_ascii=False)
        print(f"[+] stash: {stash_path}")
        # 2) потом лог на месте (сериализация заранее, чтобы ошибка не оставила обрезанный файл)
        data["chunkedPrompt"]["chunks"] = new_chunks
        out = json.dumps(data, indent=2, ensure_ascii=False)
        f.seek(0)
        f.write(out)
        f.truncate()
        print(f"[+] лог перезаписан на месте: {a.log}")


if __name__ == "__main__":
    main()
