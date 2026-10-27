# /// script
# requires-python = ">=3.11"
# dependencies = ["google-auth>=2.30", "requests>=2.31", "google-auth-oauthlib>=1.2"]
# ///
"""crm.py — единственный путь записи в CRM (Google Таблица) и на общий диск «Контент завод».

Назначение: агент и люди меняют учёт только через эти команды; скрипт сам даёт номера,
проверяет значения и правила плана по crm/schema.toml и пишет каждое изменение в лист «Журнал».
Правила заказа — одна функция order_problems: её вызывают add-order, set, pay и check,
поэтому правило не обойти ни сменой другого поля, ни ручной правкой таблицы (check найдёт).
Запись — без разбора значений таблицей (RAW): номер «0012-1» не станет датой, «=…» — формулой, «+7…» — числом.
Вход:  uv run crm/crm.py <команда> ...   (список команд: uv run crm/crm.py -h)
Выход: JSON в stdout. Ошибка: «ОШИБКА: …» в stderr и код выхода 1 — ничего не записано.
Ключ доступа лежит ВНЕ репозитория (schema.toml → [google]).
"""
import argparse, contextlib, copy, datetime as dt, hashlib, io, json, math, mimetypes, os, platform, re, shutil, sys, tempfile, time, tomllib
from pathlib import Path
from urllib.parse import quote

sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WORKDIR = REPO / "_work"
SCHEMA_FILE = HERE / "schema.toml"
SCOPES = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
AD = {"supportsAllDrives": "true"}
CHUNK = 8 * 1024 * 1024
MAX_ROWS = 5000
DASH = r"[‐-―−]"
EPOCH = dt.date(1899, 12, 30)  # день 0 в Google Таблицах: дата хранится числом дней от него
# папки, которые синхронизируются с облаком (iCloud «Рабочий стол и Документы», OneDrive, Dropbox, Яндекс Диск…)
CLOUD = {"Desktop", "Documents", "Mobile Documents", "CloudStorage", "iCloud Drive", "Dropbox", "Google Drive",
         "Yandex.Disk.localized", "YandexDisk", "Яндекс.Диск"}


class Fail(Exception):
    pass


def die(msg):
    raise Fail(msg)


def norm(s):
    """Сравнение без ловушек: регистр, ё/е, лишние пробелы, разные тире."""
    s = str(s).strip().casefold().replace("ё", "е")
    s = re.sub(DASH, "-", s)
    return re.sub(r"\s+", " ", s)


def num(v):
    """Деньги и счётчики из ячейки: пусто или мусор → 0."""
    try:
        return int(float(str(v).replace(" ", "").replace(" ", "").replace("₽", "") or 0))
    except ValueError:
        return 0


def ddate(s):
    try:
        return dt.datetime.strptime(str(s).strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def items_of(v):
    return [x.strip() for x in str(v).split(",") if x.strip()]


def today():
    return dt.date.today().strftime("%d.%m.%Y")


# ---------- доступ к Google ----------
class Api:
    def __init__(self, cfg):
        from google.auth.transport.requests import AuthorizedSession
        self.s = AuthorizedSession(self._creds(cfg))

    @staticmethod
    def _creds(cfg):
        g = cfg["google"]
        if g["auth"] == "service_account":
            key = Path(g["key_file"]).expanduser().resolve()
            if not key.exists():
                die(f"нет ключа агента {key}. Его выдаёт Роберт (раздел «Пользователь-агент»).")
            if REPO in key.parents:
                die("ключ лежит внутри репозитория — перенесите его в ~/.config/kontent-zavod/")
            from google.oauth2 import service_account
            return service_account.Credentials.from_service_account_file(str(key), scopes=SCOPES)
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        tok = Path(g["oauth_token_file"]).expanduser()
        creds = Credentials.from_authorized_user_file(str(tok), SCOPES) if tok.exists() else None
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        if not creds or not creds.valid:
            from google_auth_oauthlib.flow import InstalledAppFlow
            client = Path(g["oauth_client_file"]).expanduser()
            if not client.exists():
                die(f"нет файла {client} (план Б, выдаёт Роберт)")
            print("Откроется браузер: войдите как ПОЛЬЗОВАТЕЛЬ-АГЕНТ, не как вы сами.", file=sys.stderr)
            creds = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES).run_local_server(port=0)
            tok.parent.mkdir(parents=True, exist_ok=True)
            tok.write_text(creds.to_json())
            tok.chmod(0o600)
        return creds

    def call(self, method, url, ok=(200,), raw=False, **kw):
        for attempt in range(6):
            r = self.s.request(method, url, timeout=300, **kw)
            if r.status_code in ok:
                return r if raw else (r.json() if r.content else {})
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            try:
                m = r.json()["error"]["message"]
            except Exception:
                m = r.text[:300]
            hint = {401: "ключ отозван или неверен", 403: "у агента нет прав на этот файл/диск", 404: "не найдено: проверьте ID в schema.toml и доступ агента"}.get(r.status_code, "")
            die(f"Google ответил {r.status_code}: {m} {('— ' + hint) if hint else ''}")


# ---------- схема и таблица ----------
class Crm:
    def __init__(self, api, cfg):
        self.api, self.cfg, self.g, self.rules = api, cfg, cfg["google"], cfg["rules"]
        self.sheets = {s["name"]: s for s in cfg["sheets"]}
        if not self.g["spreadsheet_id"] or not self.g["drive_id"]:
            die("в schema.toml не заполнены drive_id / spreadsheet_id — Роберт запускает setup")
        self.sid = self.g["spreadsheet_id"]
        self.root = self.g["drive_id"]   # от какой папки строятся «Заказы», «Клиенты»…; у пробного прогона — своя
        self.data, self._idx = None, {}
        self.who = os.environ.get("KZ_WHO") or platform.node()
        self.cmd = " ".join(sys.argv[1:])[:300]

    # чтение всей CRM одним запросом
    def load(self):
        params = [("ranges", f"'{n}'") for n in self.sheets]
        params += [("valueRenderOption", "UNFORMATTED_VALUE"), ("dateTimeRenderOption", "FORMATTED_STRING")]
        r = self.api.call("GET", f"{SHEETS}/{self.sid}/values:batchGet", params=params)
        self.data, self._idx = {}, {}
        for name, vr in zip(self.sheets, r.get("valueRanges", [])):
            rows = vr.get("values", [])
            header = [str(h).strip() for h in (rows[0] if rows else [])]
            want = [c["name"] for c in self.sheets[name]["columns"]]
            if header[:len(want)] != want:
                die(f"лист «{name}»: заголовки не совпадают со схемой.\n  в таблице: {header}\n  в схеме:   {want}\n"
                    "Записи остановлены. Верните заголовки или попросите Роберта обновить схему.")
            body = [list(map(cell, row)) + [""] * (len(header) - len(row)) for row in rows[1:]]
            for i, col in enumerate(self.sheets[name]["columns"]):
                if col["type"] == "date":  # формат ячейки потеряли — дата пришла числом дней
                    for row in body:
                        if isinstance(row[i], (int, float)) and not isinstance(row[i], bool):
                            row[i] = (EPOCH + dt.timedelta(days=int(row[i]))).strftime("%d.%m.%Y")
            self.data[name] = {"header": header, "rows": body}
        return self

    def rows(self, sheet):
        t = self.data[sheet]
        return [dict(zip(t["header"], r)) | {"_row": i + 2} for i, r in enumerate(t["rows"]) if any(v != "" for v in r)]

    # ----- номера: один вид везде (запись, поиск, фильтр, ссылки между листами) -----
    def is_id(self, sheet):
        return sheet == "Заказы" or "id_prefix" in self.sheets[sheet]

    def canon(self, sheet, s):
        """«k7», «к–0007», «К 7» → «К-0007»; «12-1» → «0012-1»; «р-5», «P5» → «R-0005». Не номер → ""."""
        s = re.sub(r"\s*-\s*|\s+", "-", re.sub(DASH, "-", str(s).strip().upper()))
        if sheet == "Заказы":
            m = re.fullmatch(r"(\d{1,4})-(\d{1,2})", s)
            return f"{int(m[1]):04d}-{int(m[2])}" if m and int(m[2]) > 0 else ""
        sh = self.sheets[sheet]
        p = sh.get("id_prefix")
        m = re.fullmatch(r"([^\d\-]+)-?(\d{1,4})", s)
        if not p or not m or m[1] not in [p, *sh.get("id_aliases", [])]:
            return ""
        return f"{p}-{int(m[2]):04d}"

    def kform(self, sheet, v):
        return self.canon(sheet, v) if self.is_id(sheet) else norm(v)

    def code_form(self, v):
        """Код источника для сравнения: код размещения — канонический, промокод — norm."""
        return self.canon("Реклама", v) or norm(v)

    def example(self, sheet):
        return "0012-1" if sheet == "Заказы" else f"{self.sheets[sheet].get('id_prefix', '')}-0007"

    def index(self, sheet):
        if sheet not in self._idx:
            kcol, idx = self.sheets[sheet]["key"], {}
            for r in self.rows(sheet):
                idx.setdefault(self.kform(sheet, r[kcol]), []).append(r)
            self._idx[sheet] = idx
        return self._idx[sheet]

    def find(self, sheet, key):
        if "key" not in self.sheets[sheet]:
            die(f"в листе «{sheet}» нет ключа — ищите через get {sheet} --where …")
        k = self.kform(sheet, key)
        if not k:
            die(f"«{key}» — не номер для листа «{sheet}» (пример: {self.example(sheet)})")
        hits = self.index(sheet).get(k, [])
        if not hits:
            die(f"в листе «{sheet}» нет записи «{k}»")
        if len(hits) > 1:
            die(f"в листе «{sheet}» две записи «{k}» (строки {[h['_row'] for h in hits]}) — исправьте вручную")
        return hits[0]

    def col(self, sheet, name):
        for c in self.sheets[sheet]["columns"]:
            if norm(c["name"]) == norm(name):
                return c
        die(f"в листе «{sheet}» нет столбца «{name}». Есть: {[c['name'] for c in self.sheets[sheet]['columns']]}")

    def products(self):
        return {norm(r["Продукт"]): r for r in self.rows("Прайс")}

    # проверка и приведение одного значения; возвращает (для показа, для записи)
    def value(self, sheet, c, v):
        v = str(v).strip()
        if v == "":
            return "", ""
        t = c["type"]
        if t == "id":
            k = self.canon(sheet, v)
            if not k:
                die(f"«{c['name']}»: «{v}» — неверный формат номера (пример: {self.example(sheet)})")
            v = k
        elif t.startswith("list:"):
            lst = self.cfg["lists"][t[5:]]
            m = [x for x in lst if norm(x) == norm(v)]
            if not m:
                die(f"«{c['name']}»: значения «{v}» нет в списке {lst}")
            v = m[0]
        elif t.startswith("ref:"):
            self.find(t[4:], v)
            v = self.canon(t[4:], v)
        elif t == "source_code":
            ad = self.canon("Реклама", v)
            if ad:
                self.find("Реклама", ad)
                v = ad
            else:
                hit = [r for r in self.rows("Партнёры") if str(r["Промокод"]).strip() and norm(r["Промокод"]) == norm(v)]
                if not hit:
                    die(f"«{c['name']}»: «{v}» — нет ни такого промокода в «Партнёрах», ни кода размещения в «Рекламе». "
                        "Заведите партнёра или размещение, или оставьте пустым и напишите в «Комментарий»")
                v = str(hit[0]["Промокод"])
        elif t == "promo":
            if self.canon("Реклама", v):
                die(f"«{c['name']}»: «{v}» похож на код размещения рекламы — выберите слово, например VOSTOK")
        elif t == "date":
            d = ddate(v)
            if not d:
                die(f"«{c['name']}»: дата «{v}» не в формате дд.мм.гггг")
            v = d.strftime("%d.%m.%Y")
        elif t in ("money", "int"):
            s = re.sub(r"[\s ₽]", "", v).replace(",", ".")
            if not re.fullmatch(r"\d+(\.0+)?", s):
                die(f"«{c['name']}»: «{v}» — нужно целое число без минуса")
            return int(float(s)), int(float(s))
        elif t == "bool":
            if norm(v) not in ("true", "false", "да", "нет", "1", "0"):
                die(f"«{c['name']}»: нужно да или нет")
            b = norm(v) in ("true", "да", "1")
            return b, b
        elif t == "url":
            if not re.match(r"https?://", v):
                die(f"«{c['name']}»: нужна ссылка, начинающаяся с https://")
        elif t == "products":
            pr = self.products()
            items = items_of(v)
            bad = [p for p in items if norm(p) not in pr]
            if bad:
                die(f"«{c['name']}»: нет в «Прайсе»: {bad}. Есть: {[r['Продукт'] for r in pr.values()]}")
            v = ", ".join(pr[norm(p)]["Продукт"] for p in items)
        if len(v) > 2000:
            die(f"«{c['name']}»: слишком длинный текст (больше 2000 знаков)")
        if "pattern" in c and not re.fullmatch(c["pattern"], v):
            die(f"«{c['name']}»: «{v}» не подходит под формат. {c.get('note', '')}")
        # запись RAW: текст остаётся текстом (формула из переписки не выполнится), дата — числом дней в формате дд.мм.гггг
        if t == "date":
            return v, (ddate(v) - EPOCH).days
        return v, v

    def raws(self, sheet, r):
        """Строка (значения для показа) → значения для записи в таблицу."""
        return {k: self.value(sheet, self.col(sheet, k), v)[1] for k, v in r.items() if not k.startswith("_")}

    def parse_sets(self, sheet, sets):
        out = {}
        for s in sets or []:
            if "=" not in s:
                die(f"--set «{s}»: нужно «Столбец=значение»")
            k, v = s.split("=", 1)
            out[self.col(sheet, k)["name"]] = v
        computed = [k for k in out if self.col(sheet, k).get("computed")]
        if computed:
            die(f"{computed} считает скрипт: цена — по «Составу», оплачено — по листу «Оплаты» (команда pay). Задать вручную нельзя")
        return out

    def a1(self, sheet, row, colname):
        i = self.data[sheet]["header"].index(colname)
        letters = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            letters = chr(65 + r) + letters
        return f"'{sheet}'!{letters}{row}"

    def log(self, sheet, key, changes):
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [[str(x) for x in (stamp, self.who, self.cmd, sheet, key, f, old, new)] for f, old, new in changes]
        if rows:
            self.append("Журнал", rows)

    def append(self, sheet, rows):
        """rows — список строк (список значений) или одна строка-словарь {столбец: значение}. Возвращает номер первой строки."""
        if isinstance(rows, dict):
            rows = [[rows.get(h, "") for h in self.data[sheet]["header"]]] if self.data else [list(rows.values())]
        rng = quote(f"'{sheet}'!A1", safe="")
        r = self.api.call("POST", f"{SHEETS}/{self.sid}/values/{rng}:append",
                          params={"valueInputOption": "RAW", "insertDataOption": "OVERWRITE"}, json={"values": rows})
        m = re.search(r"![A-Z]+(\d+)", r["updates"]["updatedRange"])
        return int(m.group(1))

    def write_cells(self, sheet, row, cells):
        data = [{"range": self.a1(sheet, row, k), "values": [[v]]} for k, v in cells.items()]
        if data:
            self.api.call("POST", f"{SHEETS}/{self.sid}/values:batchUpdate", json={"valueInputOption": "RAW", "data": data})

    def next_id(self, sheet):
        sh = self.sheets[sheet]
        nums = [int(k[-4:]) for r in self.rows(sheet) if (k := self.canon(sheet, r[sh["key"]]))]
        n = max(nums, default=0) + 1
        if n > 9999:
            die("номера кончились — нужна миграция на 5 цифр (Роберт)")
        return f"{sh['id_prefix']}-{n:04d}"

    def next_order(self, to=None):
        nums = [tuple(map(int, k.split("-"))) for r in self.rows("Заказы") if (k := self.canon("Заказы", r["Номер"]))]
        if to:
            to = str(to).strip()
            if not re.fullmatch(r"\d{1,4}", to) or not any(b == int(to) for b, _ in nums):
                die(f"заказа {to} нет — нечего дополнять")
            return f"{int(to):04d}-{max(s for b, s in nums if b == int(to)) + 1}"
        b = max((b for b, _ in nums), default=0) + 1
        if b > 9999:
            die("номера заказов кончились — нужна миграция (Роберт)")
        return f"{b:04d}-1"

    def paid_map(self):
        """Сколько пришло по каждому заказу-товару по листу «Оплаты» (возвраты вычитаются)."""
        m = {}
        for p in self.rows("Оплаты"):
            k = self.canon("Заказы", p["Номер заказа"])
            m[k] = m.get(k, 0) + num(p["Сумма, ₽"]) * (-1 if p["Вид"] == "возврат" else 1)
        return m

    def internal(self, client):
        try:
            return self.find("Клиенты", client)["Источник"] == self.rules["internal_source"]
        except Fail:
            return False

    # ---------- Диск ----------
    def child(self, parent, name, folder=None):
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"'{parent}' in parents and name = '{safe}' and trashed = false"
        if folder:
            q += f" and mimeType = '{FOLDER}'"
        r = self.api.call("GET", f"{DRIVE}/files", params={"q": q, "corpora": "drive", "driveId": self.g["drive_id"],
                          "includeItemsFromAllDrives": "true", **AD, "fields": "files(id,name,mimeType,md5Checksum,webViewLink)"})
        f = r.get("files", [])
        if len(f) > 1:
            die(f"на Диске два объекта «{name}» в одной папке — уберите дубль вручную")
        return f[0] if f else None

    def mkdir(self, parent, name):
        return self.child(parent, name, True) or self.api.call(
            "POST", f"{DRIVE}/files", params={**AD, "fields": "id,name,webViewLink"}, json={"name": name, "mimeType": FOLDER, "parents": [parent]})

    def path(self, parts, create=True):
        cur = {"id": self.root, "webViewLink": f"https://drive.google.com/drive/folders/{self.root}"}
        for p in parts:
            nxt = self.child(cur["id"], p, True)
            if not nxt and not create:
                die(f"на Диске нет папки «{'/'.join(parts)}»")
            cur = nxt or self.mkdir(cur["id"], p)
        return cur

    def folder_of(self, sheet, key):
        rec = self.find(sheet, key)
        m = re.search(r"/folders/([\w-]+)", str(rec.get("Папка", "")))
        if not m:
            die(f"у «{key}» нет ссылки на папку в столбце «Папка» — mkfolder {sheet} {key}")
        return m.group(1)

    def upload(self, parent, f, replace):
        size = f.stat().st_size
        if size == 0:
            return {"файл": f.name, "пропущен": "пустой"}
        old = self.child(parent, f.name)
        if old and not replace:
            die(f"«{f.name}» уже есть в папке. Нужна замена — добавьте --replace (старая версия останется в истории файла).")
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        hdr = {"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)}
        if old:
            init = self.api.call("PATCH", f"{UPLOAD}/files/{old['id']}", params={"uploadType": "resumable", **AD}, json={}, headers=hdr, raw=True)
        else:
            init = self.api.call("POST", f"{UPLOAD}/files", params={"uploadType": "resumable", **AD},
                                 json={"name": f.name, "parents": [parent]}, headers=hdr, raw=True)
        url, pos, res = init.headers["Location"], 0, None
        with open(f, "rb") as fh:
            while pos < size:
                fh.seek(pos)
                chunk = fh.read(CHUNK)
                r = self.api.call("PUT", url, ok=(200, 201, 308), raw=True, data=chunk,
                                  headers={"Content-Range": f"bytes {pos}-{pos + len(chunk) - 1}/{size}"})
                if r.status_code in (200, 201):
                    res = r.json()
                    break
                rng = r.headers.get("Range")
                pos = int(rng.rsplit("-", 1)[1]) + 1 if rng else 0
        if not res:
            die(f"«{f.name}»: загрузка не завершилась")
        meta = self.api.call("GET", f"{DRIVE}/files/{res['id']}", params={**AD, "fields": "id,name,md5Checksum,webViewLink"})
        if meta.get("md5Checksum") != md5(f):
            die(f"«{f.name}»: контрольная сумма на Диске не совпала с файлом — загрузите заново")
        return {"файл": f.name, "ссылка": meta["webViewLink"], "проверено": "md5"}


def cell(v):
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.startswith("'"):  # «'К-0001», вписанный руками в текстовую ячейку, — это К-0001
        return v[1:]
    return v


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def clean_name(s):
    s = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", s)
    return re.sub(r"\s+", " ", s).strip()[:60]


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def need_reason(a):
    if getattr(a, "force", False) and not a.reason:
        die("--force требует --reason «почему»")


def guard(a, probs):
    """Нарушения правил: без --force — ошибка, ничего не записано; с --force — запись в «Журнал»."""
    if probs and not a.force:
        die("; ".join(probs) + ". Обойти — только по прямому слову человека: --force --reason «почему»")
    return [("правило обойдено", p, f"--force: {a.reason}") for p in probs]


# ---------- правила заказа (план 3.3) — одно место для add-order, set, pay и check ----------
def orders_with(c, *new):
    """Все заказы, как они будут после записи строк new."""
    keys = {c.canon("Заказы", r["Номер"]) for r in new}
    return [x for x in c.rows("Заказы") if c.canon("Заказы", x["Номер"]) not in keys] + list(new)


def order_ctx(c, rows):
    """Первый заказ каждого клиента; номера заказов с правом на постоплату (первые N внешних, кроме Kwork);
    суммы «К оплате» и «Оплачено» по каждому заказу «50 на 50» целиком — предоплата считается на весь заказ."""
    live, due50, paid50 = {}, {}, {}
    for x in rows:
        b = c.canon("Заказы", x["Номер"])[:4]
        if not b or x["Статус"] == "отменён":
            continue
        live.setdefault(b, (c.canon("Клиенты", x["ID клиента"]), x["Канал"]))
        if x["Порядок оплаты"] == "50 на 50":
            due50[b] = due50.get(b, 0) + num(x["К оплате, ₽"])
            paid50[b] = paid50.get(b, 0) + num(x["Оплачено, ₽"])
    first = {}
    for b in sorted(live):
        first.setdefault(live[b][0], b)
    # УК — внутренний заказчик; Kwork платит через площадку — места постоплаты они не занимают
    ext = [b for b in sorted(live) if not c.internal(live[b][0]) and live[b][1] != "Kwork"]
    return {"first": first, "postpay": set(ext[:c.rules["postpay_limit"]]), "due50": due50, "paid50": paid50}


def prepay_need(c, due):
    return math.ceil(due * c.rules["prepay_percent"] / 100)


def discounted(c, r):
    """«К оплате» по правилам: цена по прайсу минус скидка 10% (на доп. круги правок скидка не действует)."""
    price = num(r["Цена по прайсу, ₽"])
    if r["Скидка"] != "первый заказ 10%":
        return price
    pr, rp = c.products(), norm(c.rules["revision_product"])
    extra = sum(num(pr.get(rp, {}).get("Цена, ₽", 0)) for x in items_of(r["Состав"]) if norm(x) == rp)
    return round((price - extra) * (100 - c.rules["first_discount_percent"]) / 100) + extra


def pay_status(r):
    """«Статус оплаты» по деньгам: оплачен / предоплата получена — от суммы, ожидания — как были."""
    due, paid, cur = num(r["К оплате, ₽"]), num(r["Оплачено, ₽"]), r["Статус оплаты"]
    if due and paid >= due:
        return "оплачен"
    if paid > 0:
        return cur if cur in ("предоплата получена", "ждём оплату") else "ждём оплату" if cur == "оплачен" else "предоплата получена"
    return cur if cur in ("не выставлен", "ждём предоплату", "ждём оплату", "возврат") else "не выставлен"


def order_problems(c, r, ctx, starting=False):
    """Все правила плана для строки «Заказы» r (как она будет записана). [] — всё верно.
    starting=True — заказ сейчас переводят в «в работе» (нужна предоплата полностью)."""
    R, p = c.rules, []
    st, po, disc, ps = r["Статус"], r["Порядок оплаты"], r["Скидка"], r["Статус оплаты"]
    price, due, paid = num(r["Цена по прайсу, ₽"]), num(r["К оплате, ₽"]), num(r["Оплачено, ₽"])
    items = items_of(r["Состав"])
    base = c.canon("Заказы", r["Номер"])[:4]
    client = c.canon("Клиенты", r["ID клиента"])
    internal = c.internal(client)
    if due and not items:
        p.append("«К оплате» без «Состава» — сначала укажите продукты из «Прайса»")
    pr = c.products()
    free = [x for x in items if norm(x) in pr and not num(pr[norm(x)]["Цена, ₽"])]
    if free and st != "отменён":
        p.append(f"«{', '.join(free)}» — бесплатно только внутри пакета (план 3.1), отдельно в «Состав» не добавляется; "
                 "нужно SEO — предложите «Карточку под ключ»")
    if items and price and st != "отменён":
        if disc == "нет" and due < price:
            p.append(f"«К оплате» {due} ₽ меньше цены по прайсу {price} ₽ без скидки — это эскалация к Кристине; "
                     "при её согласии «Скидка=другая» и причина в «Комментарии»")
        if disc == "первый заказ 10%" and due < discounted(c, r):
            p.append(f"«К оплате» {due} ₽ меньше, чем со скидкой 10% ({discounted(c, r)} ₽) — эскалация к Кристине")
        if disc == "другая" and not str(r["Комментарий"]).strip():
            p.append("скидка «другая» — только с согласия Кристины: причину запишите в «Комментарий»")
    if disc == "первый заказ 10%" and st != "отменён":
        if r["Кейс"] != "разрешил":
            p.append("скидка 10% — за подписку на канал «Проще» и разрешение на кейс: сначала «Кейс=разрешил»")
        if ctx["first"].get(client) != base:
            p.append("скидка «первый заказ 10%» — только на первый заказ клиента")
    if po == "постоплата" and st != "отменён" and not internal and base not in ctx["postpay"]:
        p.append(f"постоплата — только для первых {R['postpay_limit']} внешних заказов (MVP; заказы УК и Kwork не считаются); "
                 "дальше «50 на 50»")
    if po and (r["Канал"] == "Kwork") != (po == "через Kwork"):
        p.append("«через Kwork» — порядок оплаты для заказов с Kwork, и только для них")
    if st in R["work_statuses"]:
        miss = [k for k in ("Состав", "К оплате, ₽", "Порядок оплаты", "Дедлайн") if r[k] in ("", None, 0)]
        if miss:
            p.append(f"для этапа «{st}» заполните: {miss}")
        if po == "50 на 50" and due:
            # предоплата — на весь заказ: копейки от деления платежа между товарами не мешают
            od, op = ctx["due50"].get(base, due), ctx["paid50"].get(base, paid)
            need = prepay_need(c, od) if starting else 1
            if op < need:
                p.append(f"«50 на 50»: работа по заказу {base} начинается после предоплаты {prepay_need(c, od)} ₽ за весь заказ "
                         f"(к оплате {od} ₽), получено {op} ₽ — сначала внесите оплату: pay {base} --sum …")
    if st == "выполнен" and (not due or paid < due):
        p.append(f"«выполнен» — только после полной оплаты: к оплате {due} ₽, получено {paid} ₽")
    if paid and paid > due:
        p.append(f"получено {paid} ₽ больше, чем «К оплате» {due} ₽ — проверьте сумму или сначала обновите «Состав»")
    if ps == "оплачен" and (not due or paid < due):
        p.append("«Статус оплаты = оплачен», а денег пришло меньше «К оплате» — оплату вносят командой pay")
    if ps == "предоплата получена" and not (0 < paid < due):
        p.append("«предоплата получена» не сходится с суммой оплат — оплату вносят командой pay")
    if ps in ("не выставлен", "ждём предоплату") and paid > 0:
        p.append(f"«{ps}», но деньги уже пришли ({paid} ₽)")
    return p


def derive_order(c, r, given, old_items, force):
    """Что считает скрипт при записи: цену по «Составу», «К оплате» по скидке, «Статус оплаты» по деньгам."""
    items, pr = items_of(r["Состав"]), c.products()
    if "Состав" in given or (r["Цена по прайсу, ₽"] in ("", None) and all(norm(x) in pr for x in items)):
        added = list(items)
        for x in old_items:
            if x in added:
                added.remove(x)
        off = [x for x in added if pr[norm(x)]["В продаже"] != "да"]
        if off and not force:
            die(f"ещё не продаются: {off}. Отметьте «В продаже» в «Прайсе» или --force с причиной")
        r["Цена по прайсу, ₽"] = sum(num(pr[norm(x)]["Цена, ₽"]) for x in items) if items else ""
    changed = {"Состав", "Скидка"} & given.keys()
    if "К оплате, ₽" not in given and (changed or r["К оплате, ₽"] in ("", None, 0)):
        if r["Скидка"] == "другая":
            if changed:
                die("при скидке «другая» укажите новую сумму «К оплате, ₽» в той же команде")
        else:
            r["К оплате, ₽"] = discounted(c, r) if items else ""
    if "Статус оплаты" not in given:
        r["Статус оплаты"] = pay_status(r)


# ---------- команды ----------
def build(c, sheet, given):
    r = {}
    for col in c.sheets[sheet]["columns"]:
        v = given.get(col["name"], "")
        if v == "" and "default" in col:
            v = today() if col["default"] == "today" else col["default"]
        if v == "" and col.get("required") and col["type"] != "id":
            die(f"не заполнен обязательный столбец «{col['name']}»")
        r[col["name"]] = c.value(sheet, col, v)[0]
    return r


def recheck(c, sheet, row, key):
    """Два агента могли взять один номер одновременно — проверяем после записи и чиним."""
    c.load()
    kcol = c.sheets[sheet]["key"]
    same = [x["_row"] for x in c.rows(sheet) if c.kform(sheet, x[kcol]) == c.kform(sheet, key)]
    if len(same) > 1 and row != min(same):
        if sheet == "Заказы":
            new = c.next_order() if key.endswith("-1") else c.next_order(key[:4])
        else:
            new = c.next_id(sheet)
        c.write_cells(sheet, row, {kcol: new})
        c.log(sheet, new, [(kcol, key, new + " (номер был занят параллельной записью)")])
        print(f"ВНИМАНИЕ: номер {key} заняли одновременно, этой записи дан {new}", file=sys.stderr)
        return new
    return key


def ad_links(c, code):
    L, res = c.cfg.get("links", {}), {}
    if L.get("tg_bot"):
        res["бот Telegram"] = f"https://t.me/{L['tg_bot']}?start={code}"
    if L.get("tg_account"):
        res["аккаунт руководителя"] = f"https://t.me/{L['tg_account']}?text=" + quote(f"Здравствуйте! Я по рекламе, код {code}")
    if L.get("site"):
        res["сайт"] = f"{L['site']}?utm_source=ads&utm_content={code}"
    if not res:
        res["внимание"] = f"в crm/schema.toml → [links] не вписан ник Telegram. Ссылка вручную: https://t.me/<ник>?text=Здравствуйте!%20Код%20{code}"
    return res


def cmd_add(c, a):
    sheet = a.sheet
    if sheet not in c.sheets or c.sheets[sheet].get("system"):
        die(f"лист «{sheet}» не для записи. Можно: {[n for n, s in c.sheets.items() if not s.get('system')]}")
    if sheet == "Заказы":
        die("заказы заводятся командой add-order")
    if sheet == "Оплаты":
        die("оплаты вносятся командой pay — она же пересчитает «Оплачено» и «Статус оплаты»")
    c.load()
    sh = c.sheets[sheet]
    r = build(c, sheet, c.parse_sets(sheet, a.set))
    kcol, key = sh.get("key"), "строка"
    if sh.get("id_prefix"):
        r[kcol] = c.next_id(sheet)
    if kcol:
        key = r[kcol]
        if not key:
            die(f"не заполнен ключевой столбец «{kcol}»")
        if c.kform(sheet, key) in c.index(sheet):
            die(f"«{key}» уже есть в листе «{sheet}» — используйте set")
    for u in sh.get("unique", []):
        if r.get(u) and any(norm(x[u]) == norm(r[u]) for x in c.rows(sheet)):
            die(f"«{u}» = «{r[u]}» уже занят")
    row = c.append(sheet, c.raws(sheet, r))
    if sh.get("id_prefix"):
        key = recheck(c, sheet, row, key)
    res = {"лист": sheet, "ключ": key, "строка": row}
    if sheet == "Клиенты":
        f = c.mkdir(c.path(["Клиенты"])["id"], clean_name(f"{key} {r['Имя']}"))
        c.write_cells(sheet, row, {"Папка": f["webViewLink"]})
        res["папка"] = f["webViewLink"]
    if sheet == "Реклама":
        res["ссылки для поста"] = ad_links(c, key)
    c.log(sheet, key, [("*", "", "создано")])
    out(res)


def cmd_add_order(c, a):
    need_reason(a)
    c.load()
    given = c.parse_sets("Заказы", a.set)
    given["ID клиента"] = a.client
    r = build(c, "Заказы", given)
    r["Номер"] = c.next_order(a.to)
    derive_order(c, r, given, [], a.force)
    forced = guard(a, order_problems(c, r, order_ctx(c, orders_with(c, r)), starting=r["Статус"] in c.rules["work_statuses"]))
    row = c.append("Заказы", c.raws("Заказы", r))
    num_ = recheck(c, "Заказы", row, r["Номер"])
    f = c.mkdir(c.path(["Заказы"])["id"], clean_name(f"{num_} {r['Товар']}"))
    for sub in c.cfg["folders"]["order"]:
        c.mkdir(f["id"], sub)
    c.write_cells("Заказы", row, {"Папка": f["webViewLink"]})
    c.log("Заказы", num_, [("*", "", "создано")] + forced)
    res = {"номер": num_, "строка": row, "папка": f["webViewLink"], "цена по прайсу": r["Цена по прайсу, ₽"],
           "к оплате": r["К оплате, ₽"], "состав": items_of(r["Состав"])}
    ctx, b = order_ctx(c, orders_with(c, r | {"Номер": num_})), num_[:4]
    if b in ctx["due50"]:  # эту сумму называем клиенту: одна на весь заказ, со всеми его товарами
        res[f"предоплата за весь заказ {b}"] = prepay_need(c, ctx["due50"][b])
        res[f"к оплате за весь заказ {b}"] = ctx["due50"][b]
    out(res)


def cmd_set(c, a):
    need_reason(a)
    c.load()
    sheet = a.sheet
    sh = c.sheets.get(sheet)
    if not sh or sh.get("system") or "key" not in sh:
        die(f"лист «{sheet}» не меняется командой set" + (": оплаты — командой pay; ошибочную строку оплаты исправляет "
                                                          "Роберт в таблице, затем pay <номер> --recalc" if sheet == "Оплаты" else ""))
    old = c.find(sheet, a.key)
    given = c.parse_sets(sheet, a.set)
    if sh["key"] in given:
        die("ключ (номер/ID) не меняется командой set")
    r, warn, forced = dict(old), [], []
    for name, v in given.items():
        r[name] = c.value(sheet, c.col(sheet, name), v)[0]
    if sheet == "Заказы":
        frm, to = old["Статус"], r["Статус"]
        if "Статус" in given and to != frm:
            if to not in c.cfg["transitions"].get(frm, []):
                forced += guard(a, [f"переход «{frm}» → «{to}» не по правилам. Можно: {c.cfg['transitions'].get(frm)}"])
            if to == "правки" and "Круги правок" not in given:
                r["Круги правок"] = num(old["Круги правок"]) + 1
                if r["Круги правок"] > c.rules["free_revision_rounds"]:
                    warn.append(f"это {r['Круги правок']}-й круг правок — платный, 1 000 ₽. Согласуйте с клиентом и добавьте "
                                f"«{c.rules['revision_product']}» в «Состав» (сумма пересчитается сама)")
            if to == "выполнен" and not r["Сдан"]:
                r["Сдан"] = today()
        derive_order(c, r, given, items_of(old["Состав"]), a.force)
        starting = to == "в работе" and frm not in c.rules["work_statuses"]
        forced += guard(a, order_problems(c, r, order_ctx(c, orders_with(c, r)), starting))
    names = [x["name"] for x in sh["columns"]]
    changes = [(k, old[k], r[k]) for k in names if str(r[k]) != str(old[k])]
    if changes:
        c.write_cells(sheet, old["_row"], {k: c.value(sheet, c.col(sheet, k), new)[1] for k, _, new in changes})
        c.log(sheet, old[sh["key"]], changes + forced)
    for w in warn:
        print("ВНИМАНИЕ: " + w, file=sys.stderr)
    out({"лист": sheet, "ключ": old[sh["key"]], "изменено": {f: n for f, _, n in changes}, "предупреждения": warn})


def order_items(c, key):
    """«0012-1» → [строка]; «0012» → все товары заказа, кроме отменённых."""
    if re.fullmatch(r"\s*\d{1,4}\s*", str(key)):
        b = f"{int(key):04d}"
        rows = [r for r in c.rows("Заказы") if c.canon("Заказы", r["Номер"])[:4] == b and r["Статус"] != "отменён"]
        if not rows:
            die(f"заказа {b} нет")
        return rows
    return [c.find("Заказы", key)]


def cmd_pay(c, a):
    """Оплата или возврат: строка в «Оплаты», затем «Оплачено» и «Статус оплаты» в «Заказы». --recalc — только пересчёт."""
    need_reason(a)
    c.load()
    rows, paidm = order_items(c, a.order), c.paid_map()
    plan = []
    if a.recalc:
        plan = [(r, 0) for r in rows]
    else:
        if not a.sum:
            die("укажите сумму: --sum 6600")
        total = c.value("Оплаты", c.col("Оплаты", "Сумма, ₽"), a.sum)[0]
        if not total:
            die("сумма должна быть больше нуля")
        d = c.value("Оплаты", c.col("Оплаты", "Дата"), a.date or today())[0]
        if ddate(d) > dt.date.today():
            die(f"дата оплаты {d} в будущем")
        method = a.method or ("Kwork" if all(r["Канал"] == "Kwork" for r in rows) else "")
        if not method:
            die(f"укажите --method: {c.cfg['lists']['pay_method']}")
        method = c.value("Оплаты", c.col("Оплаты", "Способ"), method)[0]
        if a.refund and len(rows) > 1:
            die("возврат вносится по каждому товару отдельно: pay 0012-1 --refund …")
        left = [max(num(r["К оплате, ₽"]) - paidm.get(c.canon("Заказы", r["Номер"]), 0), 0) for r in rows]
        if not a.refund and not sum(left):
            die("по заказу нечего оплачивать: «К оплате» не заполнено или всё уже оплачено")
        weights = [1] if a.refund else left
        shares = [total * w // sum(weights) for w in weights]
        shares[weights.index(max(weights))] += total - sum(shares)
        plan = list(zip(rows, shares))
    res, warns, news = [], [], []
    for r, s in plan:
        k = c.canon("Заказы", r["Номер"])
        paid = paidm.get(k, 0) + (-s if a.refund else s)
        due = num(r["К оплате, ₽"])
        if paid < 0:
            die(f"{k}: возврат больше, чем пришло денег ({paidm.get(k, 0)} ₽)")
        if paid > due and not a.force and not a.recalc:
            die(f"{k}: будет получено {paid} ₽ — больше, чем «К оплате» {due} ₽. Проверьте сумму или сначала обновите «Состав». "
                "Иначе --force --reason")
        new = dict(r) | {"Оплачено, ₽": paid}
        new["Статус оплаты"] = "возврат" if a.refund and paid == 0 else pay_status(new)
        if s:
            c.append("Оплаты", c.raws("Оплаты", {"Дата": d, "Номер заказа": k, "Сумма, ₽": s,
                                                 "Вид": "возврат" if a.refund else "оплата", "Способ": method, "Комментарий": a.comment or ""}))
        cells = {"Оплачено, ₽": paid, "Статус оплаты": new["Статус оплаты"]}
        changes = [(f, r[f], v) for f, v in cells.items() if str(r[f]) != str(v)]
        if changes:
            c.write_cells("Заказы", r["_row"], cells)
        c.log("Заказы", k, ([("возврат" if a.refund else "оплата", "", f"{s} ₽ ({method}, {d})")] if s else []) + changes)
        news.append(new)
        res.append({"номер": k, "внесено": s, "оплачено": paid, "к оплате": due, "осталось": max(due - paid, 0), "статус оплаты": new["Статус оплаты"]})
    ctx = order_ctx(c, orders_with(c, *news))  # правила — по заказу целиком, когда внесены все доли платежа
    warns = [f"{c.canon('Заказы', n['Номер'])}: {p}" for n in news for p in order_problems(c, n, ctx)]
    for w in warns:
        print("ВНИМАНИЕ: " + w, file=sys.stderr)
    out({"заказы": res, "предупреждения": warns})


def cmd_mkfolder(c, a):
    """Папка для строки, заведённой руками (без агента)."""
    c.load()
    if a.sheet not in ("Заказы", "Клиенты"):
        die("папки бывают только у «Заказы» и «Клиенты»")
    rec = c.find(a.sheet, a.key)
    if rec["Папка"]:
        die(f"папка уже есть: {rec['Папка']}")
    key = c.canon(a.sheet, rec[c.sheets[a.sheet]["key"]])
    title = rec["Товар"] if a.sheet == "Заказы" else rec["Имя"]
    f = c.mkdir(c.path([a.sheet])["id"], clean_name(f"{key} {title}"))
    if a.sheet == "Заказы":
        for sub in c.cfg["folders"]["order"]:
            c.mkdir(f["id"], sub)
    c.write_cells(a.sheet, rec["_row"], {"Папка": f["webViewLink"]})
    c.log(a.sheet, key, [("Папка", "", f["webViewLink"])])
    out({"ключ": key, "папка": f["webViewLink"]})


def cmpform(c, sheet, col):
    t = col["type"]
    if t == "id":
        return lambda v: c.canon(sheet, v)
    if t.startswith("ref:"):
        return lambda v: c.canon(t[4:], v)
    if t == "source_code":
        return c.code_form
    return norm


def cmd_get(c, a):
    c.load()
    if a.sheet not in c.sheets:
        die(f"нет листа «{a.sheet}»")
    rows = [c.find(a.sheet, a.key)] if a.key else c.rows(a.sheet)
    for w in a.where or []:
        m = re.fullmatch(r"(.+?)(!=|=)(.*)", w)
        if not m:
            die(f"--where «{w}»: нужно «Столбец=значение» или «Столбец!=значение»")
        col = c.col(a.sheet, m.group(1))
        f = cmpform(c, a.sheet, col)
        want = f(m.group(3))
        rows = [r for r in rows if (f(r[col["name"]]) == want) == (m.group(2) == "=")]
    out(rows)


def target(c, a, create=True):
    if a.order:
        root, allowed = c.folder_of("Заказы", a.order), c.cfg["folders"]["order"]
    elif a.client:
        root, allowed = c.folder_of("Клиенты", a.client), None
    else:
        parts = [p for p in a.path.split("/") if p]
        if not parts or parts[0] not in c.cfg["folders"]["root"]:
            die(f"--path должен начинаться с одной из папок {c.cfg['folders']['root']}")
        return c.path(parts, create)["id"]
    sub = [p for p in (a.sub or "").split("/") if p]
    if allowed and (not sub or sub[0] not in allowed):
        die(f"--sub должен начинаться с одной из папок заказа {allowed}")
    cur = root
    for p in sub:
        nxt = c.mkdir(cur, p) if create else c.child(cur, p, True)
        if not nxt:
            die(f"нет папки «{a.sub}»")
        cur = nxt["id"]
    return cur


def cmd_upload(c, a):
    c.load()
    paths = [Path(n).expanduser() for n in a.files]
    for p in paths:
        if not p.exists():
            die(f"нет файла {p}")
        if a.move and WORKDIR.resolve() not in p.resolve().parents:
            die(f"--move — только для файлов из _work/ (оригиналы в других папках не удаляю): {p}")
    dest, res, done = target(c, a), [], []
    for p in paths:
        files = [f for f in sorted(p.rglob("*")) if f.is_file() and not f.name.startswith(".")] if p.is_dir() else [p]
        for f in files:
            parent = dest
            if p.is_dir():
                for part in f.relative_to(p.parent).parts[:-1]:
                    parent = c.mkdir(parent, part)["id"]
            res.append(c.upload(parent, f, a.replace))
            done.append(f)
    c.log("Диск", a.order or a.client or a.path, [("загрузка", "", ", ".join(r["файл"] for r in res))])
    if a.move:  # всё загружено и сверено по md5 — локальные копии больше не нужны
        for f in done:
            f.unlink()
        for p in paths:
            if p.is_dir():
                for d in sorted((x for x in p.rglob("*") if x.is_dir()), reverse=True):
                    if not any(d.iterdir()):
                        d.rmdir()
    out(res)


def cmd_download(c, a):
    c.load()
    dest, res = Path(a.to).expanduser(), []
    def walk(fid, local):
        page = None
        while True:
            prm = {"q": f"'{fid}' in parents and trashed = false", "corpora": "drive", "driveId": c.g["drive_id"],
                   "includeItemsFromAllDrives": "true", **AD, "fields": "nextPageToken,files(id,name,mimeType,md5Checksum)"}
            if page:
                prm["pageToken"] = page
            r = c.api.call("GET", f"{DRIVE}/files", params=prm)
            for f in r.get("files", []):
                if f["mimeType"] == FOLDER:
                    walk(f["id"], local / clean_name(f["name"]))
                elif f["mimeType"].startswith("application/vnd.google-apps"):
                    res.append({"файл": f["name"], "пропущен": "документ Google, откройте в браузере"})
                else:
                    local.mkdir(parents=True, exist_ok=True)
                    p = local / clean_name(f["name"])
                    resp = c.api.call("GET", f"{DRIVE}/files/{f['id']}", params={"alt": "media", **AD}, raw=True, stream=True)
                    with open(p, "wb") as fh:
                        for b in resp.iter_content(1 << 20):
                            fh.write(b)
                    if md5(p) != f.get("md5Checksum"):
                        die(f"«{f['name']}» скачался с ошибкой — повторите")
                    res.append({"файл": str(p)})
            page = r.get("nextPageToken")
            if not page:
                return
    walk(target(c, a, create=False), dest)
    out(res)


def cmd_share(c, a):
    need_reason(a)
    c.load()
    rec = c.find("Заказы", a.order)
    sub = "Результат" if a.what == "result" else "На согласование"
    if sub == "Результат" and (rec["Статус оплаты"] != "оплачен" or num(rec["Оплачено, ₽"]) < num(rec["К оплате, ₽"]) or not num(rec["К оплате, ₽"])) and not a.force:
        die("«Результат» отдаётся только после полной оплаты. Для просмотра клиенту — share … preview")
    f = c.child(c.folder_of("Заказы", a.order), sub, True) or die(f"нет папки «{sub}» в заказе")
    c.api.call("POST", f"{DRIVE}/files/{f['id']}/permissions", params=AD, json={"type": "anyone", "role": "reader"})
    c.log("Заказы", rec["Номер"], [("доступ по ссылке", "", sub + (f" (--force: {a.reason})" if a.force else ""))])
    out({"заказ": rec["Номер"], "папка": sub, "ссылка для клиента": f["webViewLink"]})


def cloud_part():
    """Часть пути проекта, которая синхронизируется с облаком, или пусто."""
    return next((p for p in REPO.parts if p in CLOUD or p.startswith("OneDrive")), "")


def work_leftovers(c):
    """Папки _work/<номер> по выполненным и отменённым заказам — копии файлов, которые уже лежат на Диске."""
    done = {c.canon("Заказы", r["Номер"]) for r in c.rows("Заказы") if r["Статус"] in ("выполнен", "отменён")}
    if not WORKDIR.is_dir():
        return []
    return [d for d in sorted(WORKDIR.iterdir()) if d.is_dir() and (m := re.match(r"(\d{4}-\d{1,2})", d.name)) and m[1] in done]


def cmd_clean(c, a):
    c.load()
    res = []
    for d in work_leftovers(c):
        if d.resolve().parent != WORKDIR.resolve():
            die(f"{d} — не папка внутри _work, не трогаю")
        mb = round(sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 2 ** 20, 1)
        shutil.rmtree(d)
        res.append({"удалено": f"_work/{d.name}", "МБ": mb})
    out(res or [{"итог": "удалять нечего"}])


def cmd_check(c, a):
    c.load()
    errors, warns = [], []
    for name, sh in c.sheets.items():
        if sh.get("system"):
            continue
        kcol, seen = sh.get("key"), {}
        rows = c.rows(name)
        if len(rows) > MAX_ROWS - 300:
            warns.append(f"«{name}»: {len(rows)} строк из {MAX_ROWS} — Роберту расширить лист")
        for r in rows:
            k = r.get(kcol, "") if kcol else f"строка {r['_row']}"
            if kcol:
                kf = c.kform(name, k)
                if kf in seen:
                    errors.append(f"«{name}»: дубль «{k}» в строках {seen[kf]} и {r['_row']}")
                seen[kf] = r["_row"]
                if c.is_id(name) and kf and str(k) != kf:
                    errors.append(f"«{name}» строка {r['_row']}: номер «{k}» записан не в едином виде — должно быть «{kf}»")
            for col in sh["columns"]:
                try:
                    if r[col["name"]] == "" and col.get("required"):
                        raise Fail(f"не заполнен «{col['name']}»")
                    c.value(name, col, r[col["name"]])
                except Fail as e:
                    errors.append(f"«{name}» строка {r['_row']} ({k}): {e}")
    for sheet in ("Клиенты", "Заказы"):
        for r in c.rows(sheet):
            if not r["Папка"]:
                warns.append(f"«{sheet}» {r[c.sheets[sheet]['key']]}: нет папки на Диске — mkfolder {sheet} {r[c.sheets[sheet]['key']]}")
    orders, paidm, pr = c.rows("Заказы"), c.paid_map(), c.products()
    ctx, known = order_ctx(c, orders), {c.canon("Заказы", r["Номер"]) for r in orders}
    rp, free = norm(c.rules["revision_product"]), c.rules["free_revision_rounds"]
    for r in orders:
        k = r["Номер"]
        errors += [f"заказ {k}: {p}" for p in order_problems(c, r, ctx)]
        if num(r["Оплачено, ₽"]) != paidm.get(c.canon("Заказы", k), 0):
            errors.append(f"заказ {k}: «Оплачено» {num(r['Оплачено, ₽'])} ₽ не совпадает с листом «Оплаты» "
                          f"({paidm.get(c.canon('Заказы', k), 0)} ₽) — uv run crm/crm.py pay {k} --recalc")
        items = items_of(r["Состав"])
        if items and all(norm(x) in pr for x in items):
            cur = sum(num(pr[norm(x)]["Цена, ₽"]) for x in items)
            if cur != num(r["Цена по прайсу, ₽"]):
                warns.append(f"заказ {k}: цена по прайсу {num(r['Цена по прайсу, ₽'])} ₽, по нынешнему прайсу {cur} ₽ "
                             "(прайс менялся после заказа или «Состав» правили руками — тогда set … «Состав=…»)")
        extra = num(r["Круги правок"]) - free
        if extra > 0 and sum(norm(x) == rp for x in items) < extra:
            warns.append(f"заказ {k}: {num(r['Круги правок'])} круга правок, а платных «{c.rules['revision_product']}» в «Составе» меньше {extra}")
        if r["Статус"] == "выполнен" and not r["Сдан"]:
            warns.append(f"заказ {k}: «выполнен» без даты «Сдан»")
        d = ddate(r["Дедлайн"])
        if d and r["Статус"] not in ("выполнен", "отменён", "согласовано") and d < dt.date.today():
            warns.append(f"заказ {k}: дедлайн {r['Дедлайн']} прошёл, статус «{r['Статус']}»")
    for p in c.rows("Оплаты"):
        if c.canon("Заказы", p["Номер заказа"]) not in known:
            errors.append(f"«Оплаты» строка {p['_row']}: нет заказа «{p['Номер заказа']}»")
    left = work_leftovers(c)
    if left:
        warns.append(f"в _work/ лежат копии файлов закрытых заказов ({', '.join(d.name for d in left)}) — uv run crm/crm.py clean")
    if cloud_part():
        warns.append(f"папка проекта лежит в синхронизируемой папке «{cloud_part()}» — перенесите в ~/kontent-zavod (см. CLAUDE.md)")
    out({"ошибки": errors, "предупреждения": warns, "итог": "ОК" if not errors else "ЕСТЬ ОШИБКИ"})
    if errors:
        sys.exit(1)


def period(a):
    if a.month:
        m = re.fullmatch(r"(\d{1,2})\.(\d{4})", a.month.strip())
        if not m or not 1 <= int(m[1]) <= 12:
            die("--month в виде мм.гггг, например 11.2026")
        d1 = dt.date(int(m[2]), int(m[1]), 1)
        d2 = (d1 + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        return d1, d2
    d2 = ddate(a.to) if a.to else dt.date.today()
    d1 = ddate(a.frm) if a.frm else d2 - dt.timedelta(days=6)
    if not d1 or not d2 or d1 > d2:
        die("период: --from и --to в виде дд.мм.гггг, начало не позже конца")
    return d1, d2


def works_in(c, items, pr):
    """Сколько готовых работ в «Составе»: пакет — по его продуктам; бесплатное и доп. круг правок — не работа."""
    rp, n = norm(c.rules["revision_product"]), 0
    for x in items:
        parts = items_of(pr.get(norm(x), {}).get("Состав пакета", "")) or [x]
        n += sum(1 for y in parts if norm(y) != rp and num(pr.get(norm(y), {}).get("Цена, ₽", 0)) > 0)
    return n


def cmd_report(c, a):
    """Отчёт: деньги по дате поступления, новые заказы, сдачи, реклама по кодам, партнёры, расходы, обложки."""
    c.load()
    d1, d2 = period(a)
    inp = lambda v: (d := ddate(v)) is not None and d1 <= d <= d2
    R = c.rules
    clients = {c.canon("Клиенты", r["ID клиента"]): r for r in c.rows("Клиенты")}
    orders = {c.canon("Заказы", r["Номер"]): r for r in c.rows("Заказы")}
    who = lambda o: c.canon("Клиенты", orders[o]["ID клиента"]) if o in orders else ""
    is_uk = lambda cid: clients.get(cid, {}).get("Источник") == R["internal_source"]
    pays = [(p["Дата"], c.canon("Заказы", p["Номер заказа"]), num(p["Сумма, ₽"]) * (-1 if p["Вид"] == "возврат" else 1), p["Способ"])
            for p in c.rows("Оплаты")]
    got = [(o, s, m) for d, o, s, m in pays if inp(d)]
    methods = {}
    for o, s, m in got:
        methods[m] = methods.get(m, 0) + s
    by_code = {}
    for cid, cl in clients.items():
        if str(cl["Код источника"]).strip():
            by_code.setdefault(c.code_form(cl["Код источника"]), []).append(cid)
    new = [r for o, r in orders.items() if inp(r["Дата"])]
    ctx = order_ctx(c, list(orders.values()))
    first = ctx["first"]
    repeat = lambda o: o in orders and first.get(who(o)) not in (None, o[:4])  # не первый заказ этого клиента
    sources = {}
    for r in new:
        s = clients.get(c.canon("Клиенты", r["ID клиента"]), {}).get("Источник", "?")
        x = sources.setdefault(s, {"заказы": set(), "повторные": set(), "к оплате": 0})
        o = c.canon("Заказы", r["Номер"])
        x["заказы"].add(o[:4])
        if repeat(o):
            x["повторные"].add(o[:4])
        x["к оплате"] += num(r["К оплате, ₽"])
    month_start = d2.replace(day=1)
    month_ext = sum(s for d, o, s, m in pays if (x := ddate(d)) and month_start <= x <= d2 and not is_uk(who(o)))
    ads = []
    for ad in c.rows("Реклама"):
        code = c.canon("Реклама", ad["Код размещения"])
        cl = by_code.get(code, [])
        spend = num(ad["Расход, ₽"])
        ads.append({"код": code, "канал": ad["Канал или кампания"], "дата": ad["Дата"], "расход": spend, "просмотры": num(ad["Просмотры"]),
                    "обращения всего": len(cl), "обращения за период": sum(inp(clients[x]["Дата первого обращения"]) for x in cl),
                    "заказы всего": len({o[:4] for o in orders if who(o) in cl}),
                    "оплачено всего": sum(s for d, o, s, m in pays if who(o) in cl),
                    "цена обращения": round(spend / len(cl)) if cl else None})
    label = f"{d2.month:02d}.{d2.year}"
    partners = []
    for pt in c.rows("Партнёры"):
        if not str(pt["Промокод"]).strip():
            continue
        pid = c.canon("Партнёры", pt["ID партнёра"])
        cl = by_code.get(norm(pt["Промокод"]), [])
        money = sum(s for o, s, m in got if who(o) in cl and not is_uk(who(o)))
        mine = {c.canon("Заказы", r["Номер"]) for r in new if who(c.canon("Заказы", r["Номер"])) in cl}
        row = {"партнёр": pid, "название": pt["Название"], "статус": pt["Статус"], "промокод": pt["Промокод"], "клиенты": len(cl),
               "заказы за период": len({o[:4] for o in mine}), "из них повторные": len({o[:4] for o in mine if repeat(o)}),
               "деньги от его клиентов за период": money, f"к выплате ({R['partner_percent']}%)": round(money * R["partner_percent"] / 100)}
        if a.month:
            row["уже выплачено за месяц"] = sum(num(v["Сумма, ₽"]) for v in c.rows("Выплаты")
                                                if c.canon("Партнёры", v["ID партнёра"]) == pid and v["За месяц"] == label)
        partners.append(row)
    covers = {}
    for cv in c.rows("Пробные обложки"):
        if inp(cv["Дата"]):
            covers[cv["Статус"]] = covers.get(cv["Статус"], 0) + 1
    costs = {}
    for e in c.rows("Расходы"):
        if inp(e["Дата"]):
            costs[e["Статья"]] = costs.get(e["Статья"], 0) + num(e["Сумма, ₽"])
    pr = c.products()
    done = sum(works_in(c, items_of(r["Состав"]), pr) for r in orders.values() if inp(r["Сдан"]))
    ai = costs.get(R["ai_expense"], 0)
    gross = sum(s for o, s, m in got if not is_uk(who(o)))
    kwork_fee = round(methods.get("Kwork", 0) * R["kwork_fee_percent"] / 100)
    out({"период": f"{d1:%d.%m.%Y} – {d2:%d.%m.%Y}",
         "как считается": "деньги — по дате поступления (лист «Оплаты»), возвраты вычтены; УК — отдельно; "
                          f"партнёру — {R['partner_percent']}% от денег, пришедших от его клиентов за период",
         "деньги пришли (внешние)": gross,
         "деньги пришли (УК)": sum(s for o, s, m in got if is_uk(who(o))),
         "по способам": methods,
         f"комиссия Kwork ({R['kwork_fee_percent']}%)": kwork_fee,
         "на счёт (внешние минус комиссия Kwork)": gross - kwork_fee,
         "к цели месяца (внешние деньги с 1-го числа)": month_ext, "цель месяца": R["goal_month_rub"],
         f"заказов MVP (внешние, кроме УК и Kwork) из {R['postpay_limit']}": len(ctx["postpay"]),
         "новые заказы": {s: {"заказов": len(x["заказы"]), "из них повторные": len(x["повторные"]), "к оплате": x["к оплате"]}
                          for s, x in sources.items()},
         "сдано товаров": sum(inp(r["Сдан"]) for r in orders.values()),
         "в работе сейчас": sum(r["Статус"] in R["work_statuses"] and r["Статус"] != "выполнен" for r in orders.values()),
         "ждём денег": sum(max(num(r["К оплате, ₽"]) - num(r["Оплачено, ₽"]), 0) for r in orders.values()
                           if r["Статус"] in ("согласовано", "выполнен")),
         "реклама": ads, "партнёры": partners,
         "партнёров подключено (договорились)": sum(pt["Статус"] == "договорились" for pt in c.rows("Партнёры")),
         "расходы за период (без рекламы и выплат)": costs, "готовых работ сдано": done,
         f"стоимость единицы ({R['ai_expense']} / работы)": round(ai / done) if done else None,
         "пробные обложки за период": covers})


def cmd_selftest(c, a):
    steps = []
    def step(name, fn):
        try:
            note = fn()
            steps.append([name, "ОК" + (f": {note}" if isinstance(note, str) else "")])
        except (Exception, SystemExit) as e:  # выход вложенной команды — тоже провал шага, а не тихий выход
            steps.append([name, f"ОШИБКА: {e}"])
    def local():
        if cloud_part():
            die(f"папка проекта {REPO} лежит в «{cloud_part()}», а она синхронизируется с облаком (iCloud, OneDrive и т. п.): "
                "git там ломается, файлы клиентов копируются в личное облако. Перенесите проект в ~/kontent-zavod")
    step("папка проекта не в облачной синхронизации", local)
    step("таблица: чтение и заголовки", c.load)
    step("таблица: запись в «Журнал»", lambda: c.log("Журнал", "selftest", [("проверка", "", "ок")]))
    box = {}
    step("диск: папка", lambda: box.update(f=c.path(["_проверка агента"])))
    tmp = Path(tempfile.gettempdir()) / "проверка-агента.txt"
    tmp.write_text(f"проверка {dt.datetime.now()}", encoding="utf-8")
    step("диск: загрузка и сверка md5", lambda: c.upload(box["f"]["id"], tmp, True))
    tmp.unlink()
    def share():
        p = c.api.call("POST", f"{DRIVE}/files/{box['f']['id']}/permissions", params=AD, json={"type": "anyone", "role": "reader"})
        c.api.call("DELETE", f"{DRIVE}/files/{box['f']['id']}/permissions/{p['id']}", ok=(200, 204), params=AD, raw=True)
    step("диск: доступ к папке по ссылке (нужна роль «Менеджер контента»)", share)
    if a.full:
        step("пробный прогон всех записей в отдельной таблице (живой учёт не трогается)", lambda: sandbox(c))
    out(steps)
    if any(not s[1].startswith("ОК") for s in steps):
        sys.exit(1)


def ns(**kw):
    """Аргументы команды для пробного прогона — как из командной строки."""
    base = dict(force=False, reason="", set=None, to=None, comment=None, date=None, method=None, refund=False, recalc=False,
                sub="", replace=False, move=False, order=None, client=None, path=None, files=[], frm=None, month=None,
                where=None, key=None, sheet=None, what=None, sum=None)
    return argparse.Namespace(**(base | kw))


def quiet(fn, t, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            fn(t, ns(**kw))
        except SystemExit:
            die(f"{fn.__name__}: {buf.getvalue()[-600:]}")
    return json.loads(buf.getvalue())


def sandbox(c):
    """Весь путь заказа на НАСТОЯЩЕМ Google, но в отдельной таблице в «_проверка агента»: запись → чтение → сверка.
    Ловит то, чего не видит тест на имитации: как Таблица хранит номера, даты, текст «=…» и «+7…»."""
    stamp = dt.datetime.now().strftime("%d.%m.%Y %H-%M-%S")
    run = c.api.call("POST", f"{DRIVE}/files", params={**AD, "fields": "id,name,webViewLink"},  # всегда новая папка, даже в ту же секунду
                     json={"name": f"прогон {stamp}", "mimeType": FOLDER, "parents": [c.path(["_проверка агента"])["id"]]})
    f = c.api.call("POST", f"{DRIVE}/files", params={**AD, "fields": "id"}, json={"name": f"CRM проверка {stamp} — можно удалить",
                   "mimeType": "application/vnd.google-apps.spreadsheet", "parents": [run["id"]]})
    cfg = copy.deepcopy(c.cfg)
    cfg["google"]["spreadsheet_id"] = f["id"]
    t = Crm(c.api, cfg)
    t.root = run["id"]
    configure(t)
    day, dl = today(), (dt.date.today() + dt.timedelta(days=7)).strftime("%d.%m.%Y")
    k = quiet(cmd_add, t, sheet="Клиенты", set=["Имя=Проверка", "Канал связи=Telegram", "Контакт=+79990000000",
                                                "Источник=другое", "Комментарий==1+1"])["ключ"]
    order = ["Канал=Telegram личный", "Товар=Пробный товар", "Состав=Карточка под ключ", "Порядок оплаты=50 на 50", f"Дедлайн={dl}"]
    quiet(cmd_add_order, t, client="к1", set=order)
    second = quiet(cmd_add_order, t, client=k, to="1", set=order)
    prepay = second["предоплата за весь заказ 0001"]
    quiet(cmd_pay, t, order="1", sum=str(prepay), method="Продамус")
    for n in ("0001-1", "0001-2"):
        quiet(cmd_set, t, sheet="Заказы", key=n, set=["Статус=в работе"])
    tmp = Path(tempfile.mkdtemp())
    (tmp / "проверка.txt").write_text("проверка " + stamp, encoding="utf-8")
    quiet(cmd_upload, t, order="0001-1", sub="ТЗ", files=[str(tmp / "проверка.txt")])
    quiet(cmd_download, t, order="0001-1", sub="ТЗ", to=str(tmp / "скачано"))
    if (tmp / "скачано" / "проверка.txt").read_text(encoding="utf-8") != "проверка " + stamp:
        die("файл, скачанный с Диска, не совпал с загруженным")
    shutil.rmtree(tmp)
    quiet(cmd_report, t)
    quiet(cmd_check, t)
    t.load()  # сверка: что записали — то и прочитали
    cl, o1, o2 = t.find("Клиенты", "К-0001"), t.find("Заказы", "0001-1"), t.find("Заказы", "0001-2")
    want = [(k, "К-0001"), (cl["ID клиента"], "К-0001"), (cl["Контакт"], "+79990000000"), (cl["Комментарий"], "=1+1"),
            (cl["Дата первого обращения"], day), (o1["Номер"], "0001-1"), (o2["Номер"], "0001-2"), (o1["Дата"], day),
            (o1["Дедлайн"], dl), (o1["К оплате, ₽"], 13199), (num(o1["Оплачено, ₽"]) + num(o2["Оплачено, ₽"]), prepay),
            (o2["Статус"], "в работе"), ([p["Дата"] for p in t.rows("Оплаты")], [day, day])]
    bad = [f"записали «{w}», прочитали «{g}»" for g, w in want if g != w]
    if bad:
        die("Таблица хранит значения не так, как ждёт скрипт — живой учёт не начинать, показать Роберту: " + "; ".join(bad))
    return f"https://docs.google.com/spreadsheets/d/{t.sid} — откройте и посмотрите даты, списки и подсказки"


def cmd_setup(a):
    """Только Роберт: создаёт/проверяет таблицу и папки. Повторный запуск ничего не ломает."""
    cfg = tomllib.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    api, g = Api(cfg), cfg["google"]
    if not g["drive_id"]:
        drives = api.call("GET", f"{DRIVE}/drives", params={"pageSize": 10}).get("drives", [])
        if len(drives) != 1:
            die(f"агент состоит в {len(drives)} общих дисках: {[d['name'] for d in drives]}. Впишите drive_id вручную")
        g["drive_id"] = drives[0]["id"]
        save_id("drive_id", g["drive_id"])
    if not g["spreadsheet_id"]:
        f = api.call("POST", f"{DRIVE}/files", params=AD, json={"name": "CRM Контент завод",
                     "mimeType": "application/vnd.google-apps.spreadsheet", "parents": [g["drive_id"]]})
        g["spreadsheet_id"] = f["id"]
        save_id("spreadsheet_id", f["id"])
    c = Crm(api, cfg)
    report = configure(c)
    for p in cfg["folders"]["root"]:
        c.path([p])
    report.append("папки верхнего уровня: на месте")
    c.log("Журнал", "setup", [("setup", "", "; ".join(report))])
    out({"drive_id": g["drive_id"], "spreadsheet_id": c.sid, "таблица": f"https://docs.google.com/spreadsheets/d/{c.sid}", "итог": report})


def configure(c):
    """Листы, заголовки, подсказки, форматы, списки, защита, стартовый прайс. Повторный запуск ничего не ломает."""
    api, cfg = c.api, c.cfg
    meta = api.call("GET", f"{SHEETS}/{c.sid}", params={"fields": "sheets.properties(sheetId,title)"})
    have = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    reqs = [{"updateSpreadsheetProperties": {"properties": {"locale": "ru_RU", "timeZone": "Europe/Moscow"}, "fields": "locale,timeZone"}}]
    names = list(c.sheets)
    fresh = [n for n in names if n not in have]
    if len(have) == 1 and list(have)[0] in ("Sheet1", "Лист1") and fresh:
        reqs.append({"updateSheetProperties": {"properties": {"sheetId": list(have.values())[0], "title": fresh[0]}, "fields": "title"}})
        have = {fresh[0]: list(have.values())[0]}
    for n in names:
        if n not in have:
            reqs.append({"addSheet": {"properties": {"title": n}}})
    api.call("POST", f"{SHEETS}/{c.sid}:batchUpdate", json={"requests": reqs})
    meta = api.call("GET", f"{SHEETS}/{c.sid}", params={"fields": "sheets.properties(sheetId,title)"})
    ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    vals = api.call("GET", f"{SHEETS}/{c.sid}/values:batchGet", params=[("ranges", f"'{n}'!1:1") for n in names])
    report, reqs = [], []
    for n, vr in zip(names, vals["valueRanges"]):
        cols, sid = c.sheets[n]["columns"], ids[n]
        header = [str(h).strip() for h in (vr.get("values") or [[]])[0]]
        want = [x["name"] for x in cols]
        if header[:len(want)] == want:
            report.append(f"{n}: уже настроен")
            continue
        if header != want[:len(header)]:
            die(f"лист «{n}» уже есть, но заголовки другие: {header}. Ничего не менял — разберитесь вручную")
        report.append(f"{n}: {'дополнен' if header else 'создан'} ({len(want)} столбцов)")
        reqs += sheet_requests(c, n, sid, cols, new=not header)
    if reqs:
        api.call("POST", f"{SHEETS}/{c.sid}:batchUpdate", json={"requests": reqs})
    c.load()
    if not c.rows("Прайс"):
        rows = [[r[0], r[1], r[2], r[3], r[4]] for r in cfg["seed"]["price"]]
        c.append("Прайс", rows)
        report.append(f"Прайс: залито {len(rows)} строк")
    return report


def sheet_requests(c, n, sid, cols, new):
    width = len(cols)
    rq = [{"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1, "rowCount": MAX_ROWS, "columnCount": max(26, width)}},
                                     "fields": "gridProperties.frozenRowCount,gridProperties.rowCount,gridProperties.columnCount"}},
          {"updateCells": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": width},
                           "rows": [{"values": [{"userEnteredValue": {"stringValue": x["name"]}, "note": x.get("note", ""),
                                                 "userEnteredFormat": {"textFormat": {"bold": True}}} for x in cols]}],
                           "fields": "userEnteredValue,note,userEnteredFormat.textFormat.bold"}}]
    if new:
        rq.append({"addProtectedRange": {"protectedRange": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                                                            "description": "Заголовки: меняет только Роберт через схему", "warningOnly": True}}})
    if c.sheets[n].get("system"):
        return rq
    for i, x in enumerate(cols):
        rng = {"sheetId": sid, "startRowIndex": 1, "endRowIndex": MAX_ROWS, "startColumnIndex": i, "endColumnIndex": i + 1}
        t, fmt, cond = x["type"], {"type": "TEXT"}, None
        if t == "date":
            fmt, cond = {"type": "DATE", "pattern": "dd.mm.yyyy"}, {"type": "DATE_IS_VALID"}
        elif t == "money":
            fmt, cond = {"type": "NUMBER", "pattern": '#,##0 "₽"'}, {"type": "NUMBER_GREATER_THAN_EQ", "values": [{"userEnteredValue": "0"}]}
        elif t == "int":
            fmt, cond = {"type": "NUMBER", "pattern": "0"}, {"type": "NUMBER_GREATER_THAN_EQ", "values": [{"userEnteredValue": "0"}]}
        elif t == "bool":
            fmt, cond = None, {"type": "BOOLEAN"}
        elif t.startswith("list:"):
            cond = {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in c.cfg["lists"][t[5:]]]}
        elif t.startswith("ref:"):
            cond = {"type": "ONE_OF_RANGE", "values": [{"userEnteredValue": f"='{t[4:]}'!$A$2:$A"}]}
        if fmt:
            rq.append({"repeatCell": {"range": rng, "cell": {"userEnteredFormat": {"numberFormat": fmt}}, "fields": "userEnteredFormat.numberFormat"}})
        if cond:
            rq.append({"setDataValidation": {"range": rng, "rule": {"condition": cond, "strict": True, "showCustomUi": True}}})
        if (t == "id" or x.get("computed")) and new:
            rq.append({"addProtectedRange": {"protectedRange": {"range": rng, "description": "Заполняет скрипт", "warningOnly": True}}})
    return rq


def save_id(field, value):
    s = SCHEMA_FILE.read_text(encoding="utf-8")
    s2 = re.sub(rf'^{field} = ""', f'{field} = "{value}"', s, count=1, flags=re.M)
    if s2 == s:
        die(f"не смог вписать {field} в schema.toml — впишите вручную: {value}")
    SCHEMA_FILE.write_text(s2, encoding="utf-8")
    print(f"вписал {field} в crm/schema.toml — закоммитьте этот файл", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(prog="crm.py", description="Учёт «Контент завод». Подробности — CLAUDE.md.")
    sp = p.add_subparsers(dest="cmd", required=True)
    def force(x):
        x.add_argument("--force", action="store_true", help="обойти правило (только по прямому указанию человека)")
        x.add_argument("--reason", default="", help="почему обходим правило — попадёт в «Журнал»")
    sp.add_parser("check", help="проверить всю CRM (только чтение)")
    x = sp.add_parser("selftest", help="проверить доступы агента (пишет только в «Журнал» и «_проверка агента»)")
    x.add_argument("--full", action="store_true", help="ещё и пробный прогон заказа в отдельной таблице в «_проверка агента»")
    sp.add_parser("setup", help="Роберт: создать или проверить таблицу и папки")
    sp.add_parser("clean", help="удалить из _work/ копии файлов выполненных и отменённых заказов (на Диске всё остаётся)")
    x = sp.add_parser("get", help="прочитать строки: get Заказы [номер] [--where 'Статус=в работе']")
    x.add_argument("sheet"); x.add_argument("key", nargs="?"); x.add_argument("--where", action="append")
    x = sp.add_parser("add", help="новая строка: add Клиенты --set 'Имя=Анна' ...")
    x.add_argument("sheet"); x.add_argument("--set", action="append")
    x = sp.add_parser("add-order", help="новый заказ-товар с папкой: add-order --client К-0001 [--to 0012] --set ...")
    x.add_argument("--client", required=True); x.add_argument("--to"); x.add_argument("--set", action="append"); force(x)
    x = sp.add_parser("set", help="изменить поля: set Заказы 0012-1 --set 'Статус=в работе'")
    x.add_argument("sheet"); x.add_argument("key"); x.add_argument("--set", action="append", required=True); force(x)
    x = sp.add_parser("pay", help="оплата: pay 0012-1 --sum 6600 [--date дд.мм.гггг] [--method …] [--refund] | pay 0012-1 --recalc")
    x.add_argument("order"); x.add_argument("--sum"); x.add_argument("--date"); x.add_argument("--method"); x.add_argument("--comment")
    x.add_argument("--refund", action="store_true"); x.add_argument("--recalc", action="store_true"); force(x)
    x = sp.add_parser("report", help="отчёт: report (7 дней) | report --from дд.мм.гггг --to дд.мм.гггг | report --month мм.гггг")
    x.add_argument("--from", dest="frm"); x.add_argument("--to"); x.add_argument("--month")
    for name in ("upload", "download"):
        x = sp.add_parser(name, help="файлы: --order 0012-1 --sub ТЗ | --client К-0001 | --path 'Обучение/…'")
        g = x.add_mutually_exclusive_group(required=True)
        g.add_argument("--order"); g.add_argument("--client"); g.add_argument("--path")
        x.add_argument("--sub", default="")
        if name == "upload":
            x.add_argument("files", nargs="+"); x.add_argument("--replace", action="store_true")
            x.add_argument("--move", action="store_true", help="после проверки md5 удалить локальные копии (только из _work/)")
        else:
            x.add_argument("--to", required=True, help="локальная папка")
    x = sp.add_parser("mkfolder", help="папка для строки, заведённой руками: mkfolder Заказы 0012-1")
    x.add_argument("sheet"); x.add_argument("key")
    x = sp.add_parser("share", help="ссылка клиенту: share 0012-1 preview|result")
    x.add_argument("order"); x.add_argument("what", choices=["preview", "result"]); force(x)
    a = p.parse_args()
    try:
        cfg = tomllib.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        if a.cmd == "setup":
            return cmd_setup(a)
        c = Crm(Api(cfg), cfg)
        {"check": cmd_check, "selftest": cmd_selftest, "get": cmd_get, "add": cmd_add, "add-order": cmd_add_order,
         "set": cmd_set, "pay": cmd_pay, "report": cmd_report, "clean": cmd_clean, "mkfolder": cmd_mkfolder,
         "upload": cmd_upload, "download": cmd_download, "share": cmd_share}[a.cmd](c, a)
    except Fail as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ОШИБКА (непредвиденная, покажите Роберту): {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
