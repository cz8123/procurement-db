#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""供应商英文名清洗：把源系统吞掉空格的「ShenzhenXinchimediaCo.,LTD」还原成
「Shenzhen Xinchi media Co., LTD」。

**为什么能自动还原**：这批数据同一个人/公司常被录成两种写法 —— 有空格的和没空格的
（`AfloweraOu(Guangzhou)e-commercestudio` ↔ `A flower a Ou (Guangzhou) e-commerce studio`）。
把「去掉空格和标点后的小写串」当指纹，用有空格的那份当标准写法，命中率实测：
  dash_po 供应商（无空格 1,801 个）→ 可还原 1,214 个（67%）
  dash_po + fct_supplier（无空格 1,827 个）→ 可还原 1,690 个（92.5%）

剩下的多为纯拼音（`shenzhennanyouwaifurenliziyuanyouxiangongsi`）或缩写（`HZSXHLR`），
词典无从下手 —— 用 `supplier_en_overrides.json` 手工兜底（可选，格式 {"原写法": "标准写法"}）。

原则：**只改显示**。库里原文一格不动（`dash_po.Supplier_cn` / `fct_supplier.Vendor Name`），
清洗结果只出现在接口的 `en` / `supplier_en` 字段里。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
OVERRIDES = os.path.join(_APP_DIR, "supplier_en_overrides.json")

_NON_ALNUM = re.compile(r"[^0-9a-z]")
_CACHE = {"key": None, "variants": {}}
_OV_CACHE = {"key": None, "map": {}}


def compact(name: str) -> str:
    """归一化指纹：小写 + 去掉所有非字母数字（空格、标点、括号都去掉）。"""
    return _NON_ALNUM.sub("", (name or "").lower())


def split_en(name: str):
    """「中文全称-English name」取英文段；认不出返回 None（纯中文名就是 None）。"""
    s = (name or "").strip()
    i = s.find("-")
    if i <= 1 or i >= len(s) - 2:
        return None
    head, tail = s[:i].strip(), s[i + 1:].strip()
    zh = lambda x: any("\u4e00" <= ch <= "\u9fa5" for ch in x)
    en = lambda x: any(ch.isascii() and ch.isalpha() for ch in x)
    if zh(head) and en(tail) and not zh(tail):
        return tail
    if en(head) and zh(tail) and not zh(head):
        return head
    return None


def english_variants(con) -> dict:
    """指纹 → 带空格的标准写法（同一指纹取空格最多的那份）。按库文件 mtime 缓存。"""
    key = None
    try:
        row = con.execute("PRAGMA database_list").fetchone()
        db = row[2] if row else ""
        key = os.path.getmtime(db) if db and os.path.exists(db) else None
    except (sqlite3.Error, OSError):
        key = None
    if _CACHE["key"] == key and _CACHE["variants"]:
        return _CACHE["variants"]

    out = {}
    for name in _distinct_names(con):
        en = split_en(name)
        if not en or " " not in en:
            continue
        k = compact(en)
        old = out.get(k)
        if old is None or en.count(" ") > old.count(" "):
            out[k] = en
    _CACHE["key"] = key
    _CACHE["variants"] = out
    return out


def _distinct_names(con):
    """库里出现过的供应商名字（两处都是「中文-English」写法）。"""
    sqls = ('SELECT DISTINCT "Supplier_cn" FROM dash_po WHERE "Supplier_cn" IS NOT NULL',
            'SELECT DISTINCT "Vendor Name" FROM fct_supplier WHERE "Vendor Name" IS NOT NULL')
    for sql in sqls:
        try:
            for r in con.execute(sql):
                if r[0]:
                    yield r[0]
        except sqlite3.Error:
            continue


def overrides() -> dict:
    """手工兜底表（可选文件，改完不用重启也能生效：按 mtime 缓存）。"""
    try:
        key = os.path.getmtime(OVERRIDES)
    except OSError:
        _OV_CACHE.update(key=None, map={})
        return _OV_CACHE["map"]
    if _OV_CACHE["key"] != key:
        try:
            with open(OVERRIDES, encoding="utf-8") as f:
                _OV_CACHE["map"] = {str(k): str(v) for k, v in json.load(f).items()}
        except (ValueError, OSError):
            _OV_CACHE["map"] = {}
        _OV_CACHE["key"] = key
    return _OV_CACHE["map"]


def clean_en(en: str, variants=None) -> str:
    """给英文名补空格：手工表优先，其次找同指纹的带空格写法，找不到原样返回。"""
    if not en:
        return en
    s = en.strip()
    ov = overrides()
    if s in ov:
        return ov[s]
    if " " in s:
        return s
    if variants is None:
        return s
    return variants.get(compact(s), s)


def en_of(full_name: str, variants=None) -> str:
    """供应商全称 → 显示用英文名（可能为 None：纯中文名且没有映射）。"""
    return clean_en(split_en(full_name), variants)
