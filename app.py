from datetime import datetime
import glob
import html
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import requests
from bs4 import BeautifulSoup
from flask import Flask, flash, redirect, render_template, request, url_for, send_from_directory
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

log_level_str = os.getenv("logLevel", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)

logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True
)
logger = logging.getLogger("chessBotController")

active_bots = {}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PLAYERS_DIR = os.path.join(DATA_DIR, "players")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
SUBSCRIPTIONS_DIR = os.path.join(BASE_DIR, "subscriptions")
TOURNAMENTS_CACHE_FILE = os.path.join(CACHE_DIR, "tournaments_cache.json")

VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip().strip('"').strip("'")

os.makedirs(PLAYERS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(SUBSCRIPTIONS_DIR, exist_ok=True)


def fix_encoding(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    try:
        text = text.encode("iso-8859-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return " ".join(text.replace("\xa0", " ").split())


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = fix_encoding(text)
    text = unicodedata.normalize("NFKD", text)
    return " ".join(text.split()).lower()


def calculate_total_points(round_history: list) -> float:
    total = 0.0
    for item in round_history:
        res = item.get("result", "")
        if res == "1 - 0":
            total += 1.0
        elif res == "0 - 1":
            total += 0.0
        elif res in ["½ - ½", "1/2 - 1/2", "X - X"]:
            total += 0.5
    return total


def format_points(points: float) -> str:
    if points.is_integer():
        return f"{int(points)}"
    return f"{points}"


def fetch_live_result(t_id, round_num, player_name):
    action_round = f"{round_num:02d}" if round_num < 10 else str(round_num)
    url = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{t_id}/{t_id}&Action={action_round}"
    
    clean_player = clean_text(player_name)
    player_tokens = [w for w in clean_player.split() if len(w) > 2]

    try:
        resp = requests.get(url, timeout=5)
        resp.encoding = 'iso-8859-1'
        
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table", id="TablePage")
            if table:
                for row in table.find_all("tr")[1:]:
                    cells = [clean_text(el.text) for el in row.find_all("td")]
                    row_str = " ".join(cells)
                    
                    if all(token in row_str for token in player_tokens):
                        white_player = cells[2] if len(cells) > 2 else ""
                        is_white = all(token in white_player for token in player_tokens)

                        if "1 - 0" in row_str or "1-0" in row_str:
                            return "1 - 0" if is_white else "0 - 1"
                        elif "0 - 1" in row_str or "0-1" in row_str:
                            return "0 - 1" if is_white else "1 - 0"
                        elif "1/2" in row_str or "½" in row_str or "x - x" in row_str or "x-x" in row_str:
                            return "½ - ½"
    except Exception:
        pass

    return "En cours"


def clean_dead_processes():
    to_remove = [t_id for t_id, p in active_bots.items() if p.poll() is not None]
    for t_id in to_remove:
        logger.info(f"[Process] Tournament worker {t_id} finished. Cleaning up.")
        del active_bots[t_id]


def get_tournament_subscription(t_id):
    sub_file = os.path.join(SUBSCRIPTIONS_DIR, f"sub_{t_id}.json")
    if os.path.exists(sub_file):
        try:
            with open(sub_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading {sub_file}: {e}")
    return {"tournament_id": t_id, "players": {}}


def save_tournament_subscription(t_id, data):
    sub_file = os.path.join(SUBSCRIPTIONS_DIR, f"sub_{t_id}.json")
    with open(sub_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"[Subscriptions] Subscription file {sub_file} updated.")


def get_statuses():
    statuses = {}
    
    pattern = os.path.join(PLAYERS_DIR, "*", "status_*.json")
    for filepath in glob.glob(pattern):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                t_id = str(data.get("tournament_id", "")).strip()
                user_name = str(data.get("user", "")).strip()
                bot_key = f"{t_id}___{user_name}"

                if "k_factor" not in data:
                    sub = get_tournament_subscription(t_id)
                    data["k_factor"] = sub.get("players", {}).get(user_name, {}).get("k_factor", 20)

                statuses[bot_key] = data
        except Exception:
            continue

    sub_pattern = os.path.join(SUBSCRIPTIONS_DIR, "sub_*.json")
    for sub_file in glob.glob(sub_pattern):
        try:
            with open(sub_file, "r", encoding="utf-8") as f:
                sub_data = json.load(f)
                t_id = str(sub_data.get("tournament_id", "")).strip()
                for user_name, p_info in sub_data.get("players", {}).items():
                    bot_key = f"{t_id}___{user_name}"
                    if bot_key not in statuses:
                        statuses[bot_key] = {
                            "tournament_id": t_id,
                            "tournament_name": "Initialisation du tournoi...",
                            "user": user_name,
                            "k_factor": p_info.get("k_factor", 20),
                            "current_round": 1,
                            "total_rounds": "?",
                            "status": "Démarrage du worker en cours...",
                            "current_points": 0.0,
                            "delta_elo": 0.0,
                            "performance": 0,
                            "final_rank": 0,
                            "total_players": 0,
                            "category_rank": 0,
                            "category_total": 0,
                            "category_code": "",
                            "category_name": "",
                            "match_info": None,
                            "round_history": []
                        }
        except Exception:
            continue

    return statuses


def load_cached_tournaments() -> list:
    if os.path.exists(TOURNAMENTS_CACHE_FILE):
        try:
            with open(TOURNAMENTS_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[Cache] Error loading {TOURNAMENTS_CACHE_FILE}: {e}")
    return []


def scrape_and_cache_players_for_tournament(t_id: str) -> list:
    players_cache_file = os.path.join(CACHE_DIR, f"players_{t_id}.json")
    
    url = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{t_id}/{t_id}&Action=GA"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    players = []
    seen_keys = set()

    try:
        resp = requests.get(url, headers=headers, timeout=8)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "html.parser", from_encoding=resp.encoding)
            table = soup.find("table", id="TablePage") or soup.find("table", id="ctl00_ContentPlaceHolderMain_TableCalendrier")
            
            if not table:
                for t in soup.find_all("table"):
                    if t.find("td"):
                        table = t
                        break

            if table:
                rows = table.find_all("tr")[1:]
                for row in rows:
                    row_text = fix_encoding(row.text)

                    match = re.search(r'([A-Z\s\-\']{2,25}\s+[A-ZÀ-ÖØ-ß][a-zà-öø-ÿ\-\']+)', row_text)
                    if match:
                        clean_name = " ".join(match.group(1).split())
                        
                        if len(clean_name) > 3 and not any(kw in clean_name for kw in ["Ronde", "Table", "Fide", "Club"]):
                            key = clean_name.lower()
                            if key not in seen_keys:
                                seen_keys.add(key)
                                players.append(clean_name)

    except Exception as e:
        logger.error(f"[Players] Erreur lors du scraping des joueurs pour {t_id}: {e}")

    payload = {"id": t_id, "players": sorted(players)}

    if players:
        try:
            with open(players_cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info(f"[Cache/Players] Sauvegardé {len(players)} joueur(s) propres dans {players_cache_file}")
        except Exception as e:
            logger.error(f"[Cache/Players] Erreur écriture cache joueurs {t_id}: {e}")

    return sorted(players)


def fetch_and_cache_tournaments():
    logger.info("[Cache] Synchronisation globale du calendrier (par département)...")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    tournaments = []
    seen_ids = set()

    departments = [
        "01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
        "11", "12", "13", "14", "15", "16", "17", "18", "19", "2A", "2B",
        "21", "22", "23", "24", "25", "26", "27", "28", "29", "30",
        "31", "32", "33", "34", "35", "36", "37", "38", "39", "40",
        "41", "42", "43", "44", "45", "46", "47", "48", "49", "50",
        "51", "52", "53", "54", "55", "56", "57", "58", "59", "60",
        "61", "62", "63", "64", "65", "66", "67", "68", "69", "70",
        "71", "72", "73", "74", "75", "76", "77", "78", "79", "80",
        "81", "82", "83", "84", "85", "86", "87", "88", "89", "90",
        "91", "92", "93", "94", "95", "971", "972", "973", "974", "976"
    ]

    for dept in departments:
        url = f"https://www.echecs.asso.fr/ListeTournois.aspx?Action=TOURNOICOMITE&ComiteRef={dept}"
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            resp.encoding = resp.apparent_encoding or 'utf-8'
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.content, "html.parser", from_encoding=resp.encoding)
            table = soup.find("table", id="TablePage") or soup.find("table", id="ctl00_ContentPlaceHolderMain_TableCalendrier")
            if not table:
                for t in soup.find_all("table"):
                    if t.find("a", href=re.compile(r'(FicheTournoi\.aspx\?Ref=\d+|Resultats\.aspx\?URL=Tournois/Id/\d+)')):
                        table = t
                        break

            if not table:
                continue

            rows = table.find_all("tr")[1:]
            for row in rows:
                cells = [fix_encoding(el.text) for el in row.find_all("td")]
                if not cells or len(cells) < 2:
                    continue

                link_tag = row.find("a", href=re.compile(r'(FicheTournoi\.aspx\?Ref=\d+|Resultats\.aspx\?URL=Tournois/Id/\d+)'))
                if link_tag:
                    href = link_tag.get("href", "")
                    ref_match = re.search(r'(?:Ref=|Id/)(\d+)', href)
                    if ref_match:
                        tourn_id = ref_match.group(1)
                        if tourn_id in seen_ids:
                            continue
                        seen_ids.add(tourn_id)

                        title = fix_encoding(link_tag.text)
                        location = cells[1] if len(cells) > 1 else ""
                        dept_cell = cells[2] if len(cells) > 2 else dept
                        dates = cells[4] if len(cells) > 4 else (cells[3] if len(cells) > 3 else "")

                        tournaments.append({
                            "id": tourn_id,
                            "title": title,
                            "location": location,
                            "dept": dept_cell,
                            "dates": dates
                        })
        except Exception as e:
            logger.debug(f"[Cache] Erreur lors du scraping du dép {dept}: {e}")

    if tournaments:
        with open(TOURNAMENTS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(tournaments, f, ensure_ascii=False, indent=2)
        logger.info(f"[Cache] Synchronisation globale réussie : {len(tournaments)} tournois enregistrés dans {TOURNAMENTS_CACHE_FILE}")
    else:
        logger.warning("[Cache] Aucun tournoi extrait")


def schedule_tournament_sync():
    def worker():
        if not os.path.exists(TOURNAMENTS_CACHE_FILE):
            fetch_and_cache_tournaments()
        while True:
            time.sleep(6 * 3600)
            fetch_and_cache_tournaments()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()


schedule_tournament_sync()


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------
@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')


@app.route("/")
def index():
    clean_dead_processes()
    statuses = get_statuses()
    
    is_admin = (request.args.get("admin") == "1")

    last_sync = "Jamais"
    if os.path.exists(TOURNAMENTS_CACHE_FILE):
        try:
            mtime = os.path.getmtime(TOURNAMENTS_CACHE_FILE)
            last_sync = datetime.fromtimestamp(mtime).strftime("%d/%m/%Y à %H:%M")
        except Exception:
            pass

    defaults = {
        "tournament_id": os.getenv("tournament_id", ""),
        "user": os.getenv("user", ""),
        "rounds": os.getenv("rounds", "7"),
        "pushover_app_token": os.getenv("pushover_app_token", ""),
        "pushover_user_key": os.getenv("pushover_user_key", ""),
        "dry_run": os.getenv("dry_run", "False"),
    }
    return render_template(
        "index.html", 
        active_bots=active_bots, 
        statuses=statuses, 
        defaults=defaults, 
        last_sync=last_sync,
        is_admin=is_admin
    )


@app.route("/api/get_tournament_details/<t_id>")
def get_tournament_details(t_id):
    url = f"https://www.echecs.asso.fr/FicheTournoi.aspx?Ref={t_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    rounds = None
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "html.parser", from_encoding=resp.encoding)
            page_text = fix_encoding(soup.get_text())

            match = re.search(r'(?:nbr|nombre)?\s*de?\s*rondes?\s*[:\s]*(\d+)', page_text, re.IGNORECASE)
            if not match:
                match = re.search(r'(\d+)\s*rondes?', page_text, re.IGNORECASE)

            if match:
                rounds = int(match.group(1))
    except Exception as e:
        logger.error(f"[Details] Error fetching details for tournament {t_id}: {e}")

    return {"id": t_id, "rounds": rounds}


@app.route("/api/get_tournament_players/<t_id>")
def get_tournament_players(t_id):
    players_cache_file = os.path.join(CACHE_DIR, f"players_{t_id}.json")

    if os.path.exists(players_cache_file):
        try:
            with open(players_cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    players = scrape_and_cache_players_for_tournament(t_id)
    return {"id": t_id, "players": players}


@app.route("/api/search_tournaments")
def search_tournaments():
    query = request.args.get("q", "").strip().lower()
    dept = request.args.get("dept", "").strip()
    month_filter = request.args.get("date", "").strip().lower()

    cached_list = load_cached_tournaments()
    results = []

    for t in cached_list:
        full_text = f"{t.get('title', '')} {t.get('location', '')} {t.get('dept', '')} {t.get('dates', '')}".lower()

        if dept:
            dept_code = dept.zfill(2) if (dept.isdigit() and len(dept) == 1) else dept
            if dept_code not in full_text:
                continue

        if query:
            if not all(token in full_text for token in query.split()):
                continue

        if month_filter:
            month_map = {
                "janvier": ["janv", "01/"], "février": ["fevr", "02/"], "mars": ["mars", "03/"],
                "avril": ["avr", "04/"], "mai": ["mai", "05/"], "juin": ["juin", "06/"],
                "juillet": ["juil", "07/"], "août": ["aout", "août", "08/"], "septembre": ["sept", "09/"],
                "octobre": ["oct", "10/"], "novembre": ["nov", "11/"], "décembre": ["déc", "dec", "12/"]
            }

            matched_month = False
            search_terms = month_map.get(month_filter, [month_filter])
            for term in search_terms:
                if term in full_text:
                    matched_month = True
                    break

            if not matched_month:
                continue

        results.append({
            "id": t["id"],
            "title": t["title"],
            "location": f"{t['location']} ({t['dept']})" if t.get('dept') else t['location'],
            "dates": t["dates"]
        })

        if len(results) >= 15:
            break

    return {"tournaments": results}


@app.route("/api/sync_now", methods=["POST"])
def sync_now():
    threading.Thread(target=fetch_and_cache_tournaments, daemon=True).start()
    flash("Actualisation globale du calendrier lancée en arrière-plan !")
    return redirect(url_for("index"))


@app.route("/api/subscribe_webpush", methods=["POST"])
def subscribe_webpush():
    data = request.json
    t_id = str(data.get("tournament_id", "")).strip()
    player_name = str(data.get("player", "")).strip()
    push_subscription = data.get("subscription")

    if not t_id or not player_name or not push_subscription:
        return {"status": "error", "message": "Données manquantes"}, 400

    sub = get_tournament_subscription(t_id)
    if player_name in sub.get("players", {}):
        sub["players"][player_name]["webpush_subscription"] = push_subscription
        sub["players"][player_name]["enable_webpush"] = True
        save_tournament_subscription(t_id, sub)
        logger.info(f"[WebPush] Souscription enregistrée pour {player_name} (Tournoi {t_id})")
        return {"status": "success"}

    return {"status": "error", "message": "Joueur non trouvé"}, 404


@app.route("/api/active_bots")
def api_active_bots():
    clean_dead_processes()
    statuses = get_statuses()
    active_data = []

    for bot_key, data in statuses.items():
        t_id = data.get("tournament_id")
        if t_id in active_bots:
            # Récupération sécurisée du nom du joueur
            player_name = data.get("user", "")
            if not player_name and "___" in bot_key:
                player_name = bot_key.split("___", 1)[1]

            clean_slug = player_name.replace(" ", "").replace("_", "") if player_name else "joueur"

            active_data.append({
                "bot_key": bot_key,
                "tournament_id": t_id,
                "tournament_name": data.get("tournament_name", "Chargement du nom..."),
                "user": player_name or "Joueur inconnu",
                "k_factor": data.get("k_factor", 20),
                "clean_url_name": clean_slug,
                "current_round": data.get("current_round", "?"),
                "total_rounds": data.get("total_rounds", "?"),
                "status": data.get("status", "En attente des appariements"),
                "match": data.get("match_info")
            })

    return {"active_bots_count": len(active_bots), "bots": active_data}


@app.route("/tournament/<tournament_id>/<player_slug>")
def player_view_slug(tournament_id, player_slug):
    status_info = {}
    found_player_name = None
    target_filepath = None

    target_slug = player_slug.replace(" ", "").replace("_", "").lower()

    data_files = glob.glob(os.path.join(PLAYERS_DIR, "*", "status_*.json"))
    for filepath in data_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                t_id = str(data.get("tournament_id", "")).strip()
                p_name = str(data.get("user", "")).strip()
                clean_slug = p_name.replace(" ", "").replace("_", "").lower()

                if t_id == str(tournament_id).strip() and clean_slug == target_slug:
                    status_info = data
                    found_player_name = p_name
                    target_filepath = filepath
                    break
        except Exception as e:
            logger.error(f"Error reading status file {filepath}: {e}")
            continue

    if not status_info:
        sub = get_tournament_subscription(tournament_id)
        for p_name in sub.get("players", {}):
            clean_slug = p_name.replace(" ", "").replace("_", "").lower()
            if clean_slug == target_slug:
                found_player_name = p_name
                status_info = {
                    "tournament_id": tournament_id,
                    "tournament_name": "Initialisation du tournoi...",
                    "user": p_name,
                    "current_round": 1,
                    "total_rounds": "?",
                    "status": "En attente du premier passage du worker...",
                    "current_points": 0.0,
                    "delta_elo": 0.0,
                    "performance": 0,
                    "final_rank": 0,
                    "total_players": 0,
                    "category_rank": 0,
                    "category_total": 0,
                    "category_code": "",
                    "category_name": "",
                    "match_info": None,
                    "round_history": []
                }
                break

    if not found_player_name:
        return render_template(
            "player_mobile.html",
            player=player_slug,
            status_info={"status": "Joueur ou tournoi introuvable"},
            points_display="0",
            vapid_public_key=VAPID_PUBLIC_KEY
        ), 404

    if target_filepath and os.path.exists(target_filepath):
        history = status_info.get("round_history", [])
        updated = False
        for item in history:
            if item.get("result") == "En cours":
                live_res = fetch_live_result(tournament_id, item.get("round"), found_player_name)
                if live_res != "En cours":
                    item["result"] = live_res
                    updated = True

        if updated:
            status_info["current_points"] = calculate_total_points(history)
            try:
                with open(target_filepath, "w", encoding="utf-8") as f:
                    json.dump(status_info, f, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Error writing live update: {e}")

    history = status_info.get("round_history", [])
    total_pts = calculate_total_points(history)
    points_display = format_points(total_pts)

    return render_template(
        "player_mobile.html",
        player=found_player_name,
        status_info=status_info,
        points_display=points_display,
        vapid_public_key=VAPID_PUBLIC_KEY
    )


@app.route("/start", methods=["POST"])
def start():
    clean_dead_processes()

    tournament_id = request.form.get("tournament_id", "").strip()
    user = request.form.get("user", "").strip()

    provider = request.form.get("notification_provider", "none").strip()
    enable_pushover = (provider == "pushover")
    k_factor = float(request.form.get("k_factor", "20"))
    server_url = os.getenv("SERVER_URL", "https://chess-bot.fedallica.fr")

    sub = get_tournament_subscription(tournament_id)
    sub["tournament_id"] = tournament_id
    sub["players"][user] = {
        "name": user,
        "k_factor": k_factor,
        "notification_provider": provider,
        "enable_pushover": enable_pushover,
        "pushover_app_token": request.form.get("pushover_app_token", "").strip() if enable_pushover else "",
        "pushover_user_key": request.form.get("pushover_user_key", "").strip() if enable_pushover else "",
        "SERVER_URL": server_url,
        "dry_run": bool(request.form.get("dry_run"))
    }
    save_tournament_subscription(tournament_id, sub)

    if tournament_id not in active_bots:
        env_child = os.environ.copy()
        env_child.update({
            "tournament_id": tournament_id,
            "rounds": request.form.get("rounds", "7").strip(),
            "logLevel": request.form.get("logLevel", "INFO"),
        })

        proc = subprocess.Popen([sys.executable, "run.py"], env=env_child)
        active_bots[tournament_id] = proc
        logger.info(f"[Process] New worker started (PID {proc.pid}) for Tournament ID {tournament_id}")
        flash(f"Suivi démarré pour {user} (Worker lancé pour le tournoi {tournament_id})")
    else:
        logger.info(f"[Process] Player '{user}' added to active worker for tournament {tournament_id} (PID {active_bots[tournament_id].pid})")
        flash(f"Joueur {user} ajouté au worker du tournoi {tournament_id} déjà en cours !")

    return redirect(url_for("index"))


@app.route("/stop", methods=["POST"])
def stop():
    bot_key = request.form.get("bot_key")
    if bot_key and "___" in bot_key:
        t_id, user = bot_key.split("___", 1)

        sub = get_tournament_subscription(t_id)
        if user in sub.get("players", {}):
            del sub["players"][user]
            save_tournament_subscription(t_id, sub)
            logger.info(f"[Subscriptions] Player '{user}' removed from tournament {t_id}")

        if not sub.get("players") and t_id in active_bots:
            proc = active_bots[t_id]
            if proc.poll() is None:
                proc.terminate()
                logger.info(f"[Process] No remaining subscribers for tournament {t_id}. Worker (PID {proc.pid}) stopped.")
            del active_bots[t_id]
            flash(f"Dernier joueur retiré. Worker du tournoi {t_id} arrêté.")
        else:
            flash(f"Suivi de {user} arrêté pour le tournoi {t_id}.")

    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)