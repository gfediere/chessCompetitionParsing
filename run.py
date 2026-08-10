import http.client
import json
import logging
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
logger.setLevel(getattr(logging, log_level_str, logging.INFO))

ch = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch.setFormatter(formatter)
logger.addHandler(ch)

# ---------------------------------------------------------------------------
# CONFIGURATION VIA ENV VARIABLES
# ---------------------------------------------------------------------------
app_token = os.environ.get("pushover_app_token")
user_key = os.environ.get("pushover_user_key")
tournament_id = os.environ.get("tournament_id")
round_total = os.environ.get("rounds")
player = os.environ.get("user")

server_url = os.environ.get("SERVER_URL", "https://chess-bot.fedallica.fr").rstrip("/")

round_start = int(os.environ.get("round_start", 1))
notification_players_ranking = "no-notification-players-ranking" not in os.environ

# Détection du mode Dry Run
is_dry_run = "dry-run" in os.environ or os.environ.get("dry_run") == "True"


# ---------------------------------------------------------------------------
# FUNCTIONS
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """Nettoie les espaces insécables, accents et espaces superflus."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text).replace("\xa0", " ")
    return " ".join(text.split()).lower()


def calculate_total_points(round_history: list) -> float:
    """Calcule le total cumulé de points à partir de l'historique des résultats."""
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
    """Formate le score proprement (ex: 2.5 pts au lieu de 2.50)."""
    if points.is_integer():
        return f"{int(points)}"
    return f"{points}"


def update_status(
    current_round,
    status="En cours",
    t_name="Tournoi inconnu",
    match_info=None,
    round_history=None,
):
    """Enregistre l'état du bot, les détails du match courant et les points cumulés."""
    t_id = os.environ.get("tournament_id", "unknown").strip()
    u_name = os.environ.get("user", "unknown").strip()

    clean_player = u_name.replace(" ", "_")
    status_file = f"status_{t_id}_{clean_player}.json"

    if os.path.exists(status_file):
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                if t_name == "Tournoi inconnu":
                    t_name = existing_data.get("tournament_name", t_name)
                if match_info is None:
                    match_info = existing_data.get("match_info")
                if round_history is None:
                    round_history = existing_data.get("round_history", [])
        except Exception:
            pass

    if round_history is None:
        round_history = []

    current_points = calculate_total_points(round_history)

    data = {
        "tournament_id": t_id,
        "tournament_name": t_name,
        "user": u_name,
        "current_round": current_round,
        "total_rounds": os.environ.get("rounds"),
        "status": status,
        "current_points": current_points,
        "match_info": match_info,
        "round_history": round_history,
    }

    try:
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Erreur d'écriture du fichier statut : {e}")


def push_over(message: str, url: str = None, url_title: str = None):
    """Envoyer une notification Pushover (gère le mode dry run)."""
    if is_dry_run:
        logger.info(f"🧪 [DRY RUN] Message Pushover non envoyé :\n--- MESSAGE ---\n{message}\nURL: {url}\n---------------")
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
            payload_data["url_title"] = url_title or "📱 Suivi Mobile du Joueur"

        payload = urllib.parse.urlencode(payload_data)
        headers = {"Content-type": "application/x-www-form-urlencoded"}
        conn.request("POST", "/1/messages.json", payload, headers)
        response = conn.getresponse()

        if response.status != 200:
            logger.error(f"PushOver error {response.status}: {response.reason}")
        else:
            logger.info("PushOver message sent!")
    except Exception as e:
        logger.error(f"Failed to send PushOver notification: {e}")


def check_url(
    url: str, retries: int = 3, backoff: float = 1.5
) -> BeautifulSoup | None:
    """Télécharge et parse une page FFE en forçant l'encodage ISO-8859-1."""
    attempt = 0
    while attempt < retries:
        try:
            logger.info(f"Checking URL (attempt {attempt + 1}/{retries}): {url}")
            response = requests.get(url, timeout=10)
            response.encoding = 'iso-8859-1'
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")

        except requests.exceptions.RequestException as e:
            attempt += 1
            logger.warning(
                f"Error fetching URL {url} (attempt {attempt}/{retries}): {e}"
            )
            if attempt < retries:
                sleep_time = backoff**attempt
                time.sleep(sleep_time)
            else:
                logger.error(f"All retries failed for URL: {url}")
    return None


def get_match_result(round_number: int) -> str:
    """Extrait le résultat de la partie directement sur la page de la ronde."""
    action_round = f"{round_number:02d}" if round_number < 10 else str(round_number)
    url_round = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{tournament_id}/{tournament_id}&Action={action_round}"
    result = check_url(url_round)

    if result:
        table = result.find("table", id="TablePage")
        if table:
            clean_player = clean_text(player)
            player_tokens = [w for w in clean_player.split() if len(w) > 2]

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

    return "En cours"


def wait_for_round_result(round_number: int, check_interval_sec: int = 45) -> str:
    """Sonde la page de la ronde en direct jusqu'à la publication de la feuille de partie."""
    logger.info(f"🔍 Début du suivi en direct du résultat de la Ronde {round_number}...")
    while True:
        res = get_match_result(round_number)
        if res != "En cours":
            logger.info(f"✅ Résultat de la ronde {round_number} publié en direct : {res}")
            return res
        
        time.sleep(check_interval_sec)


def check_round(round_number: int) -> tuple[str, dict]:
    """Attend la publication d'une ronde et extrait le message + les détails du match."""
    action_round = f"{round_number:02d}" if round_number < 10 else str(round_number)
    url = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{tournament_id}/{tournament_id}&Action={action_round}"

    logger.info(f"Vérification des appariements pour la ronde {round_number} (URL: {url})")

    clean_player = clean_text(player)
    player_tokens = [w for w in clean_player.split() if len(w) > 2]

    while True:
        result = check_url(url)
        if result:
            page_text = clean_text(result.get_text())
            if all(token in page_text for token in player_tokens):
                logger.info(f"Joueur '{player}' trouvé dans les appariements de la ronde {round_number} !")
                break

        logger.info(f"Ronde {round_number} non encore publiée ou joueur non trouvé. Attente 30s...")
        time.sleep(30)

    table = result.find("table", id="TablePage")
    if not table:
        logger.error(f"Tableau 'TablePage' non trouvé sur la page de la ronde {round_number}")
        return f"Erreur d'extraction de la ronde {round_number}", {}

    rows = []
    for row in table.find_all("tr")[1:]:
        cells = [el.text.strip() for el in row.find_all("td")]
        if cells:
            rows.append(cells)

    for row in rows:
        row_str = clean_text(" ".join(row))
        if all(token in row_str for token in player_tokens):
            table_num = row[0]
            white_player = row[2]
            white_elo = row[3] if len(row) > 3 else ""
            black_player = row[5] if len(row) > 5 else ""
            black_elo = row[6] if len(row) > 6 else ""

            is_white = all(token in clean_text(white_player) for token in player_tokens)
            color = "Blancs" if is_white else "Noirs"
            opponent = black_player if is_white else white_player

            match_data = {
                "round": round_number,
                "table": table_num,
                "opponent": opponent,
                "color": color,
                "result": "En cours",
            }

            white_details = get_player_details(white_player)
            black_details = get_player_details(black_player)

            message = f"Ronde: {round_number}\nTable: {table_num}\n\n"
            message += f"Joueur Blanc: {white_player}\nCatégorie: {white_details[0]}\nClub: {white_details[1]}"
            if notification_players_ranking:
                message += f"\nClassement: {white_elo}"

            message += f"\n\nJoueur Noir: {black_player}\nCatégorie: {black_details[0]}\nClub: {black_details[1]}"
            if notification_players_ranking:
                message += f"\nClassement: {black_elo}"

            logger.info(f"Match trouvé pour la ronde {round_number} :\n{message}")
            return message, match_data

    logger.warning(f"Joueur {player} non trouvé dans les lignes du tableau de la ronde {round_number}.")
    return f"Joueur {player} non trouvé dans la ronde {round_number}.", {}


def get_player_details(player_name: str) -> list[str]:
    """Récupère [Catégorie, Club] d'un joueur."""
    url = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{tournament_id}/{tournament_id}&Action=Ls"
    result = check_url(url)
    if not result:
        return ["Non trouvé", "Non trouvé"]

    table = result.find("table", id="TablePage")
    if not table:
        return ["Non trouvé", "Non trouvé"]

    clean_p = clean_text(player_name)
    tokens = [w for w in clean_p.split() if len(w) > 2]

    for row in table.find_all("tr")[1:]:
        cells = [el.text.strip() for el in row.find_all("td")]
        row_str = clean_text(" ".join(cells))
        if len(cells) > 7 and all(t in row_str for t in tokens):
            category = cells[4]
            club = cells[7]
            return [category, club]

    return ["Non trouvé", "Non trouvé"]


def get_ranking(round_num: int, type_rank: str = "full") -> str:
    """Récupère le classement général."""
    logger.info(f"Ranking type is: {type_rank}")
    url = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{tournament_id}/{tournament_id}&Action=Cl"

    results = check_url(url)
    if not results:
        return "Erreur lors de la récupération du classement."

    table = results.find("table", attrs={"id": "TablePage"})
    if not table:
        return "Tableau de classement non trouvé."

    rows = []
    for row in table.find_all("tr")[1:]:
        rows.append([el.text.strip() for el in row.find_all("td")])

    logger.info(f"Getting results for: {player}")
    category = ""
    message = ""

    clean_p = clean_text(player)
    tokens = [w for w in clean_p.split() if len(w) > 2]

    for cell in rows:
        cell_str = clean_text(" ".join(cell))
        if len(cell) > 8 and all(t in cell_str for t in tokens):
            global_ranking = cell[0]
            category = cell[4]
            points = cell[8]

            logger.info(f"Player {player} FOUND in page: {url}")
            message = f"Résultats pour {player} après la ronde {round_num}\n"

            if type_rank == "light":
                message += (
                    f"Classement Général: {global_ranking}\nPoints: {points}\n\n"
                )
            else:
                message += (
                    f"Classement Général: {global_ranking}\n"
                    f"Catégorie: {category}\n"
                    f"Points: {points}\n\n"
                )
            break

    if category:
        logger.info(f"Getting category ranking for: {category}")
        message += f"Classement pour catégorie {category}:\n"
        row_number = 0
        for cell in rows:
            if len(cell) > 8 and category in cell:
                row_number += 1
                if type_rank == "light":
                    message += f"{row_number}- {cell[2]} / Points: {cell[8]}\n"
                else:
                    message += (
                        f"{row_number}- {cell[2]}\n"
                        f"Classement Général: {cell[0]}\n"
                        f"Club: {cell[7]}\n"
                        f"Points: {cell[8]}\n\n"
                    )

    return message


def tournament_name(tourn_id: str) -> str:
    """Récupère le nom officiel du tournoi."""
    url = f"https://www.echecs.asso.fr/FicheTournoi.aspx?Ref={tourn_id}"
    results = check_url(url)
    if not results:
        return "Tournoi inconnu"

    table = results.find("table", id="ctl00_ContentPlaceHolderMain_TableTournoi")
    if table:
        first_row = table.find("tr")
        if first_row and first_row.find("td"):
            return first_row.find("td").text.strip()

    return "Tournoi inconnu"


# ---------------------------------------------------------------------------
# MAIN EXECUTION
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if is_dry_run:
        logger.info("🧪 PROGRAMME DEMARRE EN MODE DRY-RUN (AUCUNE NOTIFICATION PUSHOVER NE SERA ENVOYEE)")

    logger.info(
        f"Starting program for tournament {tournament_id} ({round_total} rounds) for {player}. Starting round: {round_start}"
    )

    t_name = tournament_name(tournament_id)
    history = []

    update_status(
        current_round=round_start,
        status="Initialisation",
        t_name=t_name,
        match_info=None,
        round_history=history,
    )

    clean_player_slug = player.replace(" ", "")
    mobile_path = f"/player/{tournament_id}/{clean_player_slug}"

    if server_url:
        full_mobile_url = f"{server_url}{mobile_path}"
    else:
        full_mobile_url = mobile_path

    start_msg = (
        f"Notifications activées pour :\n"
        f"Nom du tournoi : {t_name}\n"
        f"Joueur : {player}\n"
        f"Nombre de rondes : {round_total}"
    )
    logger.info(start_msg)

    push_over(
        start_msg,
        url=full_mobile_url,
        url_title="📱 Consulter la page de suivi mobile",
    )

    for rondeNumber in range(round_start, int(round_total) + 1):
        logger.info(f"Checking Round: {rondeNumber}")

        update_status(
            current_round=rondeNumber,
            status="En attente des appariements",
            t_name=t_name,
            match_info=None,
            round_history=history,
        )

        msg_round, match_data = check_round(rondeNumber)

        if match_data:
            history.append(match_data)

        # Ajout des points cumulés actuels au message de ronde
        current_pts = calculate_total_points(history[:-1])
        msg_round = f"Points en cours: {format_points(current_pts)} pt(s)\n\n" + msg_round

        update_status(
            current_round=rondeNumber,
            status="Match en cours",
            t_name=t_name,
            match_info=match_data,
            round_history=history,
        )

        if rondeNumber > 1:
            msg_round += "\n\n" + get_ranking(rondeNumber - 1, "light")

        logger.info(f"Sending message for round {rondeNumber}:\n{msg_round}")
        push_over(msg_round)

        # ⚡ SUIVI EN DIRECT DU RESULTAT
        round_res = wait_for_round_result(rondeNumber)
        if history:
            history[-1]["result"] = round_res

        total_pts = calculate_total_points(history)
        res_msg = (
            f"Ronde {rondeNumber} - Résultat de la partie :\n"
            f"Score : {round_res}\n"
            f"Match : {match_data.get('color', '')} vs {match_data.get('opponent', '')}\n\n"
            f"📊 Nouveau total : {format_points(total_pts)} pt(s)"
        )
        push_over(res_msg)

        update_status(
            current_round=rondeNumber,
            status="Partie terminée",
            t_name=t_name,
            match_info=match_data,
            round_history=history,
        )

    logger.info(f"Round {round_total} finished. Fetching final results...")
    
    update_status(
        current_round=int(round_total),
        status="Terminé",
        t_name=t_name,
        match_info=None,
        round_history=history,
    )

    final_ranking_msg = get_ranking(int(round_total), "full")
    logger.info(f"Final message:\n{final_ranking_msg}")
    push_over(final_ranking_msg)