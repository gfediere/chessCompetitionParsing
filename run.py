import http.client
import logging
import os
import re
import time
import urllib.parse
from bs4 import BeautifulSoup
import requests

# ---------------------------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------------------------
logger = logging.getLogger("chessCompetitionLogger")
logger.setLevel(logging.INFO)

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

round_start = int(os.environ.get("round_start", 1))
notification_players_ranking = "no-notification-players-ranking" not in os.environ

logger.info(f"Notification ranking on pairings: {notification_players_ranking}")


# ---------------------------------------------------------------------------
# FUNCTIONS
# ---------------------------------------------------------------------------
def push_over(message: str):
    """Envoyer une notification Pushover."""
    if "dry-run" in os.environ:
        logger.info("Dry run: Pushover message not sent!")
        return

    try:
        conn = http.client.HTTPSConnection("api.pushover.net:443")
        payload = urllib.parse.urlencode(
            {
                "token": app_token,
                "user": user_key,
                "message": message,
            }
        )
        headers = {"Content-type": "application/x-www-form-urlencoded"}
        conn.request("POST", "/1/messages.json", payload, headers)
        response = conn.getresponse()

        if response.status != 200:
            logger.error(f"PushOver error {response.status}: {response.reason}")
        else:
            logger.info("PushOver message sent!")
    except Exception as e:
        logger.error(f"Failed to send PushOver notification: {e}")


def check_url(url: str, retries: int = 5, backoff: float = 2.0) -> BeautifulSoup | None:
    """Télécharge et parse une page avec retries progressifs."""
    attempt = 0
    while attempt < retries:
        try:
            logger.info(f"Checking URL (attempt {attempt + 1}/{retries}): {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return BeautifulSoup(response.content, "html.parser")

        except requests.exceptions.RequestException as e:
            attempt += 1
            logger.warning(f"Error fetching URL {url} (attempt {attempt}/{retries}): {e}")
            if attempt < retries:
                sleep_time = backoff**attempt
                logger.info(f"Retrying in {sleep_time:.1f} seconds...")
                time.sleep(sleep_time)
            else:
                logger.error(f"All retries failed for URL: {url}")
    return None


def get_ranking(round_num: int, type_rank: str = "full") -> str:
    """Récupère le classement général et par catégorie."""
    logger.info(f"Ranking type is: {type_rank}")
    url = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{tournament_id}/{tournament_id}&Action=Cl"

    if type_rank == "full":
        parsed_content = f"après la ronde {round_num}"
        while True:
            results = check_url(url)
            if results and re.search(parsed_content, str(results.contents)):
                logger.info(f"Content: '{parsed_content}' found")
                break
            logger.info(f"Content: '{parsed_content}' NOT found. Waiting 60s...")
            time.sleep(60)

    results = check_url(url)
    if not results:
        return "Erreur lors de la récupération du classement."

    table = results.find("table", attrs={"id": "TablePage"})
    if not table:
        return "Tableau de classement non trouvé."

    rows = []
    for row in table.find_all("tr")[1:]:  # On saute le header (tr 0)
        rows.append([el.text.strip() for el in row.find_all("td")])

    logger.info(f"Getting results for: {player}")
    category = ""
    message = ""

    # Recherche des infos du joueur principal
    for cell in rows:
        if len(cell) > 8 and player in cell:
            global_ranking = cell[0]
            category = cell[4]
            points = cell[8]

            logger.info(f"Player {player} FOUND in page: {url}")
            message = f"Résultats pour {player} après la ronde {round_num}\n"

            if type_rank == "light":
                message += f"Classement Général: {global_ranking}\nPoints: {points}\n\n"
            else:
                message += (
                    f"Classement Général: {global_ranking}\n"
                    f"Catégorie: {category}\n"
                    f"Points: {points}\n\n"
                )
            break

    # Classement spécifique à la catégorie du joueur
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


def get_player_details(player_name: str) -> list[str]:
    """Récupère [Catégorie, Club] d'un joueur."""
    url = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{tournament_id}/{tournament_id}&Action=Ls"
    result = check_url(url)
    if not result:
        return ["Non trouvé", "Non trouvé"]

    table = result.find("table", id="TablePage")
    if not table:
        return ["Non trouvé", "Non trouvé"]

    for row in table.find_all("tr")[1:]:
        cells = [el.text.strip() for el in row.find_all("td")]
        if len(cells) > 7 and player_name in cells:
            category = cells[4]
            club = cells[7]
            logger.info(f"Player found: {player_name} | Club: {club} | Cat: {category}")
            return [category, club]

    return ["Non trouvé", "Non trouvé"]


def check_round(round_number: int) -> str:
    """Attend la publication d'une ronde et extrait l'appariement du joueur."""
    url = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{tournament_id}/{tournament_id}&Action=0{round_number}"

    while True:
        logger.debug(f"Checking pairings for round {round_number}")
        result = check_url(url)
        if result and re.search(re.escape(player), str(result.contents)):
            break
        logger.info(f"Round {round_number} not updated yet. Waiting 30s...")
        time.sleep(30)

    table = result.find("table", id="TablePage")
    if not table:
        return f"Erreur d'extraction de la ronde {round_number}"

    # Extraction propre des lignes
    rows = []
    for row in table.find_all("tr")[1:]:
        cells = [el.text.strip() for el in row.find_all("td")]
        if cells:
            rows.append(cells)

    # Recherche du match du joueur (Une seule fois !)
    for row in rows:
        if len(row) > 5 and player in row:
            table_num = row[0]
            white_player = row[2]
            white_elo = row[3] if len(row) > 3 else ""
            black_player = row[5]
            black_elo = row[6] if len(row) > 6 else ""

            white_details = get_player_details(white_player)
            black_details = get_player_details(black_player)

            message = f"Ronde: {round_number}\nTable: {table_num}\n\n"
            message += f"Joueur Blanc: {white_player}\nCatégorie: {white_details[0]}\nClub: {white_details[1]}"
            if notification_players_ranking:
                message += f"\nClassement: {white_elo}"

            message += f"\n\nJoueur Noir: {black_player}\nCatégorie: {black_details[0]}\nClub: {black_details[1]}"
            if notification_players_ranking:
                message += f"\nClassement: {black_elo}"

            logger.info(f"Match found:\n{message}")
            return message

    return f"Joueur {player} non trouvé dans la ronde {round_number}."


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
    logger.info(
        f"Starting program for tournament {tournament_id} ({round_total} rounds) for {player}. Starting round: {round_start}"
    )

    t_name = tournament_name(tournament_id)
    start_msg = (
        f"Notifications activées pour :\n"
        f"Nom du tournoi : {t_name}\n"
        f"Joueur : {player}\n"
        f"Nombre de rondes : {round_total}"
    )
    logger.info(start_msg)
    push_over(start_msg)

    # Boucle sur chaque ronde
    for rondeNumber in range(round_start, int(round_total) + 1):
        logger.info(f"Checking Round: {rondeNumber}")

        # 1. Attente et notification de l'appariement de la ronde
        msg_round = check_round(rondeNumber)

        # 2. Ajout du classement léger de la ronde précédente (si ce n'est pas la ronde 1)
        if rondeNumber > 1:
            msg_round += "\n\n" + get_ranking(rondeNumber - 1, "light")

        logger.info(f"Sending message for round {rondeNumber}:\n{msg_round}")
        push_over(msg_round)

    # Fin du tournoi : Récupération du classement général complet
    logger.info(f"Round {round_total} finished. Fetching final results...")
    final_ranking_msg = get_ranking(int(round_total), "full")
    logger.info(f"Final message:\n{final_ranking_msg}")
    push_over(final_ranking_msg)