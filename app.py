import glob
import json
import logging
import os
import re
import subprocess
import sys
import unicodedata
import requests
from bs4 import BeautifulSoup
from flask import Flask, flash, redirect, render_template, request, url_for
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
SUBSCRIPTIONS_DIR = os.path.join(BASE_DIR, "subscriptions")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SUBSCRIPTIONS_DIR, exist_ok=True)


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text).replace("\xa0", " ")
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
    
    pattern = os.path.join(DATA_DIR, "status_*.json")
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
                            "match_info": None,
                            "round_history": []
                        }
        except Exception:
            continue

    return statuses


@app.route("/")
def index():
    clean_dead_processes()
    statuses = get_statuses()
    defaults = {
        "tournament_id": os.getenv("tournament_id", ""),
        "user": os.getenv("user", ""),
        "SERVER_URL": os.getenv("SERVER_URL", "https://chess-bot.fedallica.fr"),
        "rounds": os.getenv("rounds", "7"),
        "pushover_app_token": os.getenv("pushover_app_token", ""),
        "pushover_user_key": os.getenv("pushover_user_key", ""),
        "dry_run": os.getenv("dry_run", "False"),
    }
    return render_template("index.html", active_bots=active_bots, statuses=statuses, defaults=defaults)


@app.route("/api/active_bots")
def api_active_bots():
    clean_dead_processes()
    statuses = get_statuses()
    active_data = []

    for bot_key, data in statuses.items():
        t_id = data.get("tournament_id")
        if t_id in active_bots:
            player_name = data.get("user", "")
            active_data.append({
                "bot_key": bot_key,
                "tournament_id": t_id,
                "tournament_name": data.get("tournament_name", "Chargement du nom..."),
                "user": player_name,
                "k_factor": data.get("k_factor", 20),
                "clean_url_name": player_name.replace(" ", ""),
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

    data_files = glob.glob(os.path.join(DATA_DIR, "status_*.json"))
    for filepath in data_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                t_id = str(data.get("tournament_id", "")).strip()
                p_name = str(data.get("user", "")).strip()
                clean_p_name = p_name.replace(" ", "")

                if t_id == str(tournament_id).strip() and clean_p_name == player_slug:
                    status_info = data
                    found_player_name = p_name
                    target_filepath = filepath
                    break
        except Exception:
            continue

    if not status_info:
        sub = get_tournament_subscription(tournament_id)
        for p_name in sub.get("players", {}):
            clean_p_name = p_name.replace(" ", "")
            if clean_p_name == player_slug:
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
                    "match_info": None,
                    "round_history": []
                }
                break

    if not found_player_name:
        return render_template(
            "player_mobile.html",
            player=player_slug,
            status_info={"status": "Joueur ou tournoi introuvable"},
            points_display="0"
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
    )


@app.route("/start", methods=["POST"])
def start():
    clean_dead_processes()

    tournament_id = request.form.get("tournament_id", "").strip()
    user = request.form.get("user", "").strip()

    provider = request.form.get("notification_provider", "none").strip()
    enable_pushover = (provider == "pushover")
    k_factor = float(request.form.get("k_factor", "20"))

    sub = get_tournament_subscription(tournament_id)
    sub["tournament_id"] = tournament_id
    sub["players"][user] = {
        "name": user,
        "k_factor": k_factor,
        "notification_provider": provider,
        "enable_pushover": enable_pushover,
        "pushover_app_token": request.form.get("pushover_app_token", "").strip() if enable_pushover else "",
        "pushover_user_key": request.form.get("pushover_user_key", "").strip() if enable_pushover else "",
        "SERVER_URL": request.form.get("SERVER_URL", "https://chess-bot.fedallica.fr").strip(),
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