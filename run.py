import http.client
import json
import logging
import math
import os
import re
import time
import unicodedata
import urllib.parse
from bs4 import BeautifulSoup
import requests

# ---------------------------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------------------------
logger = logging.getLogger("chessCompetitionLogger")
log_level_str = os.environ.get("logLevel", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)

logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True
)

# ---------------------------------------------------------------------------
# CONSTANTS & DIRECTORIES
# ---------------------------------------------------------------------------
INTERVAL_WAIT_PAIRINGS = 30     # 30s waiting for pairings
INTERVAL_GAME_IN_PROGRESS = 180 # 3 min while games are in progress
INTERVAL_CHECK_RESULT = 30      # 30s when waiting for results publication

tournament_id = os.environ.get("tournament_id")
round_total = int(os.environ.get("rounds", 7))
round_start = 1

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SUBSCRIPTIONS_DIR = os.path.join(BASE_DIR, "subscriptions")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SUBSCRIPTIONS_DIR, exist_ok=True)

SUBSCRIPTION_FILE = os.path.join(SUBSCRIPTIONS_DIR, f"sub_{tournament_id}.json")

# ---------------------------------------------------------------------------
# ELO & PERFORMANCE HELPERS
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text).replace("\xa0", " ")
    return " ".join(text.split()).lower()

def extract_elo(cell_text: str) -> int:
    """Extrait le premier nombre à 3 ou 4 chiffres d'une cellule texte."""
    if not cell_text:
        return 0
    match = re.search(r'\b\d{3,4}\b', cell_text)
    return int(match.group(0)) if match else 0

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

def get_expected_score(player_elo: float, opponent_elo: float) -> float:
    """Calculates FIDE expected score based on Elo rating difference."""
    diff = opponent_elo - player_elo
    return 1.0 / (1.0 + 10.0 ** (diff / 400.0))

def calculate_elo_and_perf(player_elo: float, round_history: list, k_factor: float = 20.0) -> tuple[float, int]:
    """Calculates cumulative Delta Elo and FIDE Performance."""
    if not round_history or player_elo == 0:
        return 0.0, 0

    total_delta = 0.0
    total_score = 0.0
    opponents_elo = []

    for item in round_history:
        res = item.get("result", "")
        opp_elo = float(item.get("opponent_elo", 0))

        if opp_elo == 0 or res not in ["1 - 0", "0 - 1", "½ - ½"]:
            continue

        opponents_elo.append(opp_elo)

        score = 0.0
        if res == "1 - 0":
            score = 1.0
        elif res == "½ - ½":
            score = 0.5
        elif res == "0 - 1":
            score = 0.0

        total_score += score
        expected = get_expected_score(player_elo, opp_elo)
        total_delta += k_factor * (score - expected)

    perf = 0
    if opponents_elo:
        avg_opp_elo = sum(opponents_elo) / len(opponents_elo)
        percentage = total_score / len(opponents_elo)

        if percentage >= 0.99:
            dp = 800
        elif percentage <= 0.01:
            dp = -800
        else:
            dp = -400 * math.log10((1.0 / percentage) - 1.0)

        perf = int(round(avg_opp_elo + dp))

    return round(total_delta, 1), perf

def load_subscriptions() -> dict:
    if os.path.exists(SUBSCRIPTION_FILE):
        try:
            with open(SUBSCRIPTION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.debug(f"[Subscriptions] Reading {SUBSCRIPTION_FILE} ({len(data.get('players', {}))} subscribed player(s))")
                return data
        except Exception as e:
            logger.error(f"[Subscriptions] Error reading {SUBSCRIPTION_FILE}: {e}")
    return {"players": {}}

def push_over(player_cfg: dict, message: str, url: str = None, url_title: str = None):
    player_name = player_cfg.get("name", "Unknown")

    if not player_cfg.get("enable_pushover", True):
        logger.debug(f"[Pushover] Notifications disabled for {player_name}. Skipping.")
        return

    app_token = player_cfg.get("pushover_app_token")
    user_key = player_cfg.get("pushover_user_key")

    if not app_token or not user_key:
        logger.debug(f"[Pushover] Missing token or user key for {player_name}. Skipping notification.")
        return

    if player_cfg.get("dry_run", False):
        logger.info(f"[DRY RUN] Pushover notification not sent to {player_name}:\n--- MESSAGE ---\n{message}\nURL: {url}\n---------------")
        return

    try:
        conn = http.client.HTTPSConnection("api.pushover.net:443")
        payload_data = {
            "token": app_token,
            "user": user_key,
            "message": message,
        }
        if url:
            payload_data["url"] = url
            payload_data["url_title"] = url_title or "Player Mobile Tracking"

        payload = urllib.parse.urlencode(payload_data)
        headers = {"Content-type": "application/x-www-form-urlencoded"}
        conn.request("POST", "/1/messages.json", payload, headers)
        conn.getresponse()
        logger.info(f"[Pushover] Notification successfully sent to {player_name}")
    except Exception as e:
        logger.error(f"[Pushover] Failed to send notification for {player_name}: {e}")

def check_url(url: str, retries: int = 3) -> BeautifulSoup | None:
    attempt = 0
    while attempt < retries:
        try:
            logger.debug(f"[HTTP GET] FFE request (attempt {attempt + 1}/{retries}): {url}")
            response = requests.get(url, timeout=10)
            response.encoding = 'iso-8859-1'
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except Exception as e:
            attempt += 1
            logger.warning(f"[HTTP GET] Failed for {url} ({e}) - attempt {attempt}/{retries}")
            if attempt < retries:
                time.sleep(2)
    logger.error(f"[HTTP GET] Unable to reach URL after {retries} attempts: {url}")
    return None

def update_player_status(t_id, player_name, current_round, status, t_name, match_info, round_history, k_factor=20.0, player_elo=0):
    clean_player = player_name.replace(" ", "_")
    status_file = os.path.join(DATA_DIR, f"status_{t_id}_{clean_player}.json")
    
    current_points = calculate_total_points(round_history)
    delta_elo, performance = calculate_elo_and_perf(player_elo, round_history, k_factor)

    data = {
        "tournament_id": t_id,
        "tournament_name": t_name,
        "user": player_name,
        "player_elo": player_elo,
        "k_factor": k_factor,
        "delta_elo": delta_elo,
        "performance": performance,
        "current_round": current_round,
        "total_rounds": round_total,
        "status": status,
        "current_points": current_points,
        "match_info": match_info,
        "round_history": round_history,
    }
    try:
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        logger.debug(f"[Cache/Status] Updated file {status_file} (Status: '{status}', Delta Elo: {delta_elo})")
    except Exception as e:
        logger.error(f"[Cache/Status] Error writing status for {player_name}: {e}")

def tournament_name(tourn_id: str) -> str:
    url = f"https://www.echecs.asso.fr/FicheTournoi.aspx?Ref={tourn_id}"
    soup = check_url(url)
    if soup:
        table = soup.find("table", id="ctl00_ContentPlaceHolderMain_TableTournoi")
        if table and table.find("tr"):
            name = table.find("tr").find("td").text.strip()
            logger.info(f"[Tournament] Name identified: '{name}' (ID: {tourn_id})")
            return name
    return "Unknown Tournament"

def catchup_player_history(p_name: str, p_cfg: dict, current_round: int, t_name: str) -> tuple[list, int]:
    """Fetches past rounds history for a player added mid-tournament."""
    logger.info(f"[Catchup] Fetching history for rounds 1 to {current_round - 1} for '{p_name}'...")
    
    clean_p = clean_text(p_name)
    tokens = [w for w in clean_p.split() if len(w) > 2]
    history = []
    found_player_elo = 0

    for past_round in range(round_start, current_round):
        action_round = f"{past_round:02d}" if past_round < 10 else str(past_round)
        url_past = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{tournament_id}/{tournament_id}&Action={action_round}"
        
        soup = check_url(url_past)
        if not soup:
            continue

        table = soup.find("table", id="TablePage")
        if not table:
            continue

        for row in table.find_all("tr")[1:]:
            row_cells = [el.text.strip() for el in row.find_all("td")]
            row_str = clean_text(" ".join(row_cells))

            if all(t in row_str for t in tokens):
                table_num = row_cells[0]
                white_player = row_cells[2] if len(row_cells) > 2 else ""
                white_elo = extract_elo(row_cells[3]) if len(row_cells) > 3 else 0
                black_player = row_cells[5] if len(row_cells) > 5 else ""
                black_elo = extract_elo(row_cells[6]) if len(row_cells) > 6 else 0

                is_white = all(t in clean_text(white_player) for t in tokens)
                color = "Blancs" if is_white else "Noirs"
                opponent = black_player if is_white else white_player
                opponent_elo = black_elo if is_white else white_elo
                
                curr_elo = white_elo if is_white else black_elo
                if curr_elo > 0:
                    found_player_elo = curr_elo

                result = "En cours"
                if "1 - 0" in row_str or "1-0" in row_str:
                    result = "1 - 0" if is_white else "0 - 1"
                elif "0 - 1" in row_str or "0-1" in row_str:
                    result = "0 - 1" if is_white else "1 - 0"
                elif "1/2" in row_str or "½" in row_str or "x - x" in row_str or "x-x" in row_str:
                    result = "½ - ½"

                match_data = {
                    "round": past_round,
                    "table": table_num,
                    "opponent": opponent,
                    "opponent_elo": opponent_elo,
                    "color": color,
                    "result": result
                }
                history.append(match_data)
                logger.info(f"[Catchup] Round {past_round} found for {p_name}: {color} vs {opponent} ({result})")
                break

    if history:
        pts = calculate_total_points(history)
        k_factor = p_cfg.get("k_factor", 20.0)
        delta, perf = calculate_elo_and_perf(found_player_elo, history, k_factor)
        
        msg_catchup = (
            f"Tracking activated for {p_name}!\n"
            f"Caught up rounds: {len(history)}\n"
            f"Current score: {format_points(pts)} pt(s)\n"
            f"Elo variation: {delta:+g} Elo"
        )
        clean_slug = p_name.replace(" ", "")
        mobile_url = f"{p_cfg.get('SERVER_URL')}/tournament/{tournament_id}/{clean_slug}"
        push_over(p_cfg, msg_catchup, url=mobile_url)

    return history, found_player_elo

# ---------------------------------------------------------------------------
# MAIN WORKER LOOP
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    t_name = tournament_name(tournament_id)
    logger.info(f"[Worker] Starting centralized worker for Tournament ID {tournament_id} ({t_name}) - Rounds 1 to {round_total}")

    players_state = {}

    for round_num in range(round_start, round_total + 1):
        logger.info(f"===========================================================")
        logger.info(f"[Worker] START TRACKING ROUND {round_num} / {round_total}")
        logger.info(f"===========================================================")
        
        for p_name in players_state:
            players_state[p_name]["pairing_sent"] = False
            players_state[p_name]["result_sent"] = False

        action_round = f"{round_num:02d}" if round_num < 10 else str(round_num)
        url_round = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{tournament_id}/{tournament_id}&Action={action_round}"

        round_completed_for_all = False

        while not round_completed_for_all:
            subs = load_subscriptions()
            tracked_players = subs.get("players", {})

            if not tracked_players:
                logger.info("[Worker] No active subscriptions for this tournament. Stopping worker.")
                exit(0)

            for p_name, p_cfg in tracked_players.items():
                if p_name not in players_state:
                    logger.info(f"[Subscriptions] Registering new subscribed player: '{p_name}'")
                    
                    past_history = []
                    found_elo = 0
                    if round_num > round_start:
                        past_history, found_elo = catchup_player_history(p_name, p_cfg, round_num, t_name)

                    players_state[p_name] = {
                        "history": past_history,
                        "player_elo": found_elo,
                        "pairing_sent": False,
                        "result_sent": False,
                        "match_data": None
                    }

            logger.info(f"[Scraper] Centralized fetch for Round {round_num} for {len(tracked_players)} subscriber(s)...")
            soup = check_url(url_round)
            table_rows = []
            if soup:
                table = soup.find("table", id="TablePage")
                if table:
                    table_rows = table.find_all("tr")[1:]

            all_pairings_found = True
            all_results_found = True
            any_game_in_progress = False

            for p_name, p_cfg in list(tracked_players.items()):
                state = players_state[p_name]
                clean_p = clean_text(p_name)
                tokens = [w for w in clean_p.split() if len(w) > 2]
                k_factor = float(p_cfg.get("k_factor", 20.0))

                player_row = None
                for row in table_rows:
                    row_text = clean_text(row.text)
                    if all(t in row_text for t in tokens):
                        player_row = [el.text.strip() for el in row.find_all("td")]
                        break

                if not player_row:
                    all_pairings_found = False
                    all_results_found = False
                    logger.debug(f"[Player] Pairings not yet published for {p_name} (Round {round_num})")
                    update_player_status(tournament_id, p_name, round_num, "En attente des appariements", t_name, None, state["history"], k_factor, state.get("player_elo", 0))
                    continue

                table_num = player_row[0]
                white_player = player_row[2] if len(player_row) > 2 else ""
                white_elo = extract_elo(player_row[3]) if len(player_row) > 3 else 0
                black_player = player_row[5] if len(player_row) > 5 else ""
                black_elo = extract_elo(player_row[6]) if len(player_row) > 6 else 0

                is_white = all(t in clean_text(white_player) for t in tokens)
                color = "Blancs" if is_white else "Noirs"
                opponent = black_player if is_white else white_player
                opponent_elo = black_elo if is_white else white_elo
                
                new_player_elo = white_elo if is_white else black_elo
                if new_player_elo > 0:
                    state["player_elo"] = new_player_elo
                player_elo = state.get("player_elo", 0)

                row_str = " ".join([clean_text(x) for x in player_row])
                result = "En cours"
                if "1 - 0" in row_str or "1-0" in row_str:
                    result = "1 - 0" if is_white else "0 - 1"
                elif "0 - 1" in row_str or "0-1" in row_str:
                    result = "0 - 1" if is_white else "1 - 0"
                elif "1/2" in row_str or "½" in row_str or "x - x" in row_str or "x-x" in row_str:
                    result = "½ - ½"

                match_data = {
                    "round": round_num,
                    "table": table_num,
                    "opponent": opponent,
                    "opponent_elo": opponent_elo,
                    "color": color,
                    "result": result
                }
                state["match_data"] = match_data

                # Mettre à jour la ronde courante dans l'historique
                existing_round_entry = next((item for item in state["history"] if item.get("round") == round_num), None)
                if existing_round_entry:
                    existing_round_entry.update(match_data)
                else:
                    state["history"].append(match_data)

                if not state["pairing_sent"]:
                    state["pairing_sent"] = True
                    logger.info(f"[Pairings] Pairing found for {p_name} (Board {table_num}, {color} vs {opponent})")
                    msg = f"Ronde {round_num} - Echiquier {table_num}\nJoueur: {p_name}\nCouleur: {color}\nAdversaire: {opponent} ({opponent_elo})"
                    clean_slug = p_name.replace(" ", "")
                    mobile_url = f"{p_cfg.get('SERVER_URL')}/tournament/{tournament_id}/{clean_slug}"
                    push_over(p_cfg, msg, url=mobile_url)

                if result == "En cours":
                    all_results_found = False
                    any_game_in_progress = True
                    update_player_status(tournament_id, p_name, round_num, "Match en cours", t_name, match_data, state["history"], k_factor, player_elo)
                else:
                    if not state["result_sent"]:
                        state["result_sent"] = True
                        pts = calculate_total_points(state["history"])
                        delta, perf = calculate_elo_and_perf(player_elo, state["history"], k_factor)
                        
                        logger.info(f"[Results] Result published for {p_name}: {result} (Score: {format_points(pts)} pts, Delta Elo: {delta:+g})")
                        res_msg = (
                            f"Ronde {round_num} Terminée pour {p_name} !\n"
                            f"Résultat: {result}\n"
                            f"Nouveau total: {format_points(pts)} pt(s)\n"
                            f"Variation Elo: {delta:+g} Elo (Perf: {perf})"
                        )
                        push_over(p_cfg, res_msg)

                    update_player_status(tournament_id, p_name, round_num, "Partie terminée", t_name, match_data, state["history"], k_factor, player_elo)

            if all_pairings_found and all_results_found:
                round_completed_for_all = True
                logger.info(f"[Round {round_num}] Round fully completed for all tournament subscribers.")
                break

            if not all_pairings_found:
                sleep_time = INTERVAL_WAIT_PAIRINGS
                reason = "Waiting for pairings publication"
            elif any_game_in_progress:
                sleep_time = INTERVAL_GAME_IN_PROGRESS
                reason = "Games in progress (request saving mode)"
            else:
                sleep_time = INTERVAL_CHECK_RESULT
                reason = "Waiting for results publication"

            logger.info(f"[Adaptive Polling] Pausing for {sleep_time}s ({reason})")
            time.sleep(sleep_time)

    logger.info(f"[Worker] Tournament ID {tournament_id} completely finished!")