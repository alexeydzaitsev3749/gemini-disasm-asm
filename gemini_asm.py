#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gemini_asm.py  --  ассемблер: приклеивает обратно то, что вырезал gemini_disasm.py. IN-PLACE.

Лог открывается в r+ и перезаписывается на месте (тот же inode / Drive file-id).
Приклеенное убирается из <лог>_stash.json; можно клеить по частям в любом порядке.
Когда в stash ничего не осталось, он удаляется (лог снова целый).

Запуск без критериев выбора = --list (ничего не меняет).

Что клеить:
    --all
    --with KEY ...      signatures | attachments | video | drive |
                        driveImage driveDocument driveVideo youtubeVideo inlineImage inlineFile
    --pick N ...        номера вложений из --list
    --ids ID ...        id вложений (drive / youtube)
(критерии складываются)

Примеры:
    python gemini_asm.py chat.json                        # список
    python gemini_asm.py chat.json --with signatures
    python gemini_asm.py chat.json --with video --pick 5
    python gemini_asm.py chat.json --all --backup
"""
import argparse, json, os, shutil, sys

ATTACH_KEYS = ("driveImage", "driveDocument", "driveVideo", "youtubeVideo", "inlineImage", "inlineFile")
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


def virtual_indices(n_present, absent):
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


def restore_sig(chunk, item):
    node = chunk
    for step in item["path"]:
        node = node[step]
    items = list(node.items())
    items.insert(min(item["pos"], len(items)), (item["key"], item["value"]))
    node.clear()
    node.update(items)


def main():
    ap = argparse.ArgumentParser(description="Ассемблер лога Gemini (in-place).")
    ap.add_argument("log")
    ap.add_argument("--stash")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--with", dest="with_", nargs="+", default=[], metavar="KEY")
    ap.add_argument("--pick", nargs="+", type=int, default=[], metavar="N")
    ap.add_argument("--ids", nargs="+", default=[], metavar="ID")
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    base, ext = os.path.splitext(a.log)
    stash_path = a.stash or f"{base}_stash.json"
    if not os.path.exists(stash_path):
        sys.exit(f"[!] нет stash: {stash_path} (нечего приклеивать)")
    with open(stash_path, encoding="utf-8") as sf:
        stash = json.load(sf)
    if stash.get("format") != FORMAT:
        sys.exit("[!] неизвестный формат stash")
    atts, sigs = stash["attachments"], stash["signatures"]

    with open(a.log, "r+", encoding="utf-8") as f:
        data = json.load(f)
        chunks = data["chunkedPrompt"]["chunks"]
        absent = {it["v"] for it in atts}
        vs = virtual_indices(len(chunks), absent)
        bad = verify(chunks, vs, stash["skeleton"])
        if bad is not None and not a.force:
            sys.exit(f"[!] лог не соответствует stash (чанк #{bad} другой роли/времени): его правили? --force чтобы обойти")

        kinds = expand_kinds(["all"] if a.all else a.with_)
        pick, ids = set(a.pick), set(a.ids)
        for n in pick:
            if not 1 <= n <= len(atts):
                sys.exit(f"[!] нет вложения №{n}")

        if a.list or not (kinds or pick or ids):
            sz = sum(len(s["value"]) for s in sigs if isinstance(s["value"], str))
            print(f"[*] stash: исходник '{stash['source']}', в логе сейчас {len(chunks)} чанков")
            print(f"\nВложения в stash ({len(atts)}):")
            for n, it in enumerate(atts, 1):
                print(f"  [{n:>3}] v#{it['v']:<4} {it['kind']:<13} {str(it['tokenCount']):>6} tok  {it['id']}")
            print(f"\nПодписи в stash: {len(sigs)} шт., {sz/1024:.1f} KB")
            if not a.list:
                print("[*] Файл НЕ изменён. Укажи --all / --with / --pick / --ids")
            return

        # --- подписи: чанк находим по виртуальному индексу среди присутствующих
        pos_of = {v: p for p, v in enumerate(vs)}
        sig_keep, n_sig, sig_skipped = [], 0, 0
        for it in sigs:
            if "signatures" in kinds and it["v"] in pos_of:
                restore_sig(chunks[pos_of[it["v"]]], it)
                n_sig += 1
            else:
                if "signatures" in kinds:
                    sig_skipped += 1          # чанк-хозяин сам ещё в stash
                sig_keep.append(it)

        # --- вложения: по возрастанию v; позиция = v - (сколько ещё отсутствует левее v)
        chosen_n = {n for n, it in enumerate(atts, 1)
                    if it["kind"] in kinds or n in pick or it["id"] in ids}
        remaining = set(absent)
        for n in sorted(chosen_n, key=lambda n: atts[n - 1]["v"]):
            it = atts[n - 1]
            pos = it["v"] - sum(1 for x in remaining if x < it["v"])
            chunks.insert(min(pos, len(chunks)), it["chunk"])
            remaining.discard(it["v"])
        att_keep = [it for n, it in enumerate(atts, 1) if n not in chosen_n]

        print(f"[+] приклеено: вложений {len(chosen_n)}, подписей {n_sig}; чанков в логе: {len(chunks)}")
        if sig_skipped:
            print(f"[!] {sig_skipped} подписей пропущено: их чанки ещё в stash (приклей сначала вложения)")
        if a.dry_run:
            print("[*] --dry-run: ничего не записано.")
            return

        if a.backup:
            shutil.copy2(a.log, a.log + ".bak")
            print(f"[+] бэкап: {a.log}.bak")
        # 1) сначала лог на месте, 2) потом убираем приклеенное из stash
        #    (при сбое между шагами данные дублируются, но не теряются; проверка по скелету это поймает)
        out = json.dumps(data, indent=2, ensure_ascii=False)
        f.seek(0)
        f.write(out)
        f.truncate()
        print(f"[+] лог перезаписан на месте: {a.log}")

    stash["attachments"], stash["signatures"] = att_keep, sig_keep
    if not att_keep and not sig_keep:
        os.remove(stash_path)
        print(f"[+] stash пуст -- удалён ({stash_path}); лог снова целый")
    else:
        with open(stash_path, "w", encoding="utf-8") as sf:
            json.dump(stash, sf, indent=2, ensure_ascii=False)
        print(f"[+] stash обновлён: осталось вложений {len(att_keep)}, подписей {len(sig_keep)}")


if __name__ == "__main__":
    main()
