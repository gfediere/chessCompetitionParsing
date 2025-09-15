from bs4 import BeautifulSoup
import requests
import re
import time
import os
import http.client, urllib
import logging

logger = logging.getLogger('chessCompetitionLogger')
logger.setLevel(logging.INFO)

# Create a console handler
ch = logging.StreamHandler()

# Create a formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)

# Add handlers to the logger
logger.addHandler(ch)

# Token used by PushOver notification platform
app_token = os.environ['pushover_app_token']
user_key = os.environ['pushover_user_key']

tournament_id = os.environ['tournament_id']
round_total = os.environ['rounds']
player = os.environ['user']

round_start = 1
if "round_start" in os.environ:
  round_start = os.environ['round_start']

# When tables pairings are sent, ELO's opponent could be disabled on the message
if "no-notification-players-ranking" in os.environ:
  notification_players_ranking = False
else:
  notification_players_ranking = True
logger.info("Notification ranking on pairings: " + str(notification_players_ranking))

def push_over(message): # PushOver function to send notifications
  if not "dry-run" in os.environ:
    conn = http.client.HTTPSConnection("api.pushover.net:443")
    conn.request("POST", "/1/messages.json",
      urllib.parse.urlencode({
       "token": app_token,
       "user": user_key,
       "message": message,
     }), { "Content-type": "application/x-www-form-urlencoded" })
    response=conn.getresponse()
    if response.status != 200:
      logger.error("PushOver issue, error returned: " + str(response.status) + "log: " + response.reason)
    else:
      logger.info("PushOver message sent !")
  else:
    logger.info("Dry run program PushOver not sent !")

def check_url(url: str, retries: int = 20, backoff: float = 5.0):
    """
    Fetch and parse a webpage with BeautifulSoup, retrying on errors.
    
    Args:
        url (str): The target URL.
        retries (int): Number of retry attempts before failing.
        backoff (float): Base seconds to wait between retries (exponential).
    
    Returns:
        BeautifulSoup | None: Parsed HTML or None if all retries failed.
    """
    attempt = 0
    while attempt < retries:
        try:
            logger.info("Checking URL (attempt %d/%d): %s", attempt + 1, retries, url)
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            webpage = BeautifulSoup(response.content, "html.parser")
            logger.debug("Page fetched successfully, length: %d characters", len(response.text))
            return webpage

        except requests.exceptions.RequestException as e:
            attempt += 1
            logger.warning("Error fetching URL %s (attempt %d/%d): %s", url, attempt, retries, e)
            
            if attempt < retries:
                sleep_time = backoff ** attempt
                logger.info("Retrying in %.1f seconds...", sleep_time)
                time.sleep(sleep_time)
            else:
                logger.error("All retries failed for URL: %s", url)

    return None

def get_ranking(round, type="full"): # Provide ranking for a given User
  logger.info("Ranking type is: " + type)
  results=""
  url = "http://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/"+tournament_id+"/"+tournament_id+"&Action=Cl"
  if type == "full": # 
    while True:
      results = check_url(url)
      parsed_content = "après la ronde " + str(round) # Test if ranking page up-to-date regarding round
      if re.findall(str(parsed_content), str(results.contents)):
        logger.info("Content: " + parsed_content + " found")
        break
      else:
        logger.info("Content: " + parsed_content + " NOT found")
        time.sleep(60)
        continue
  results = check_url(url)
  logger.info("Webpage up-to-date for results")
  table = results.find('table', attrs = {'id':'TablePage'}) # Get Table results
  rows = []
  for i, row in enumerate(table.find_all('tr')):
    if i == 0:
      header = [el.text.strip() for el in row.find_all('th')]
    else:
      rows.append([el.text.strip() for el in row.find_all('td')])

  logger.info("Getting results for: " + player)   
  category = ""
  message = ""   
  for cell in rows:
    if player in cell:
      points = cell[8]
      category = cell[4]
      global_ranking = cell[0]
      logger.info("Player: " + player + " FOUND in page: " + url)
      message = "Resultats pour " + player + " apres la ronde " + str(round) + "\n"

      if type == "light":
        message += "Classement General: " + global_ranking + "\n" \
        + "Points: " + points + "\n"

      else: # full ranking
        message += "Classement General: " + global_ranking + "\n" \
        + "Catégorie: " + category + "\n" \
        + "Points: " + points + "\n\n"

  logger.info("Getting results for: " + player + " in category: " + category)
  message += "Classement pour categorie " + category + "\n"
  row_number = 0
  for cell in rows:
      if category in cell:
        row_number += 1
        if type == "light":
          message += str(row_number) + "-" + cell[2] + " / Points: " + cell[8] + "\n"

        else:
          message += str(row_number) + "-" + cell[2] + "\n" \
          + "Classement General: " + cell[0] + "\n" \
          + "Club: " + cell[7] + "\n" \
          + "Points " + cell[8] + "\n\n" \
  
  logger.info(message)
  return(message)

def get_player_details(player):
  url = "https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/"+tournament_id+"/"+tournament_id+"&Action=Ls"
  result = check_url(url)
  logger.debug("Get details for player: "+ player)
  table = result.find('table', id="TablePage")

  rows = []
  category = ""
  club = ""
  for i, row in enumerate(table.find_all('tr')):
    if i == 0:
      header = [el.text.strip() for el in row.find_all('th')]
    else:
      rows.append([el.text.strip() for el in row.find_all('td')])

    for row in rows:
      if player in row:
        category = row[4]
        club = row[7]
        logger.info("Player found in details page: " + player + " Club: " + club + " Category: " + category)
        return [category,club]
      #else:
        #logger.info("Player NOT found in details page: " + player)
  return ["Non trouvé","Non trouvé"]

def check_round(round_number):
  while True:
    logger.debug("Call check_url function for round: " + str(round_number))
    url = "http://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/"+tournament_id+"/"+tournament_id+"&Action=0"+str(round_number)
    result = check_url(url)
    logger.debug("Webpage returned in while loop: " + str(result.contents))
    if re.findall(str(player), str(result.contents)):
      logger.debug("Break")
      break
    else:
      logger.info("Round webpage not updated: " + str(round_number))
      time.sleep(20) # Waiting time between URL checks
      continue

  result = check_url(url)
  logger.info("Webpage up-to-date for round: " + str(round_number))

  # first we should find our table object:
  table = result.find('table', id="TablePage")
  # then we can iterate through each row and extract either header or row values:

  rows = []
  for i, row in enumerate(table.find_all('tr')):
    if i == 0:
      header = [el.text.strip() for el in row.find_all('th')]
    else:
      rows.append([el.text.strip() for el in row.find_all('td')])

    for row in rows:
      if player in row:
        message = ""
        message += "Ronde: " + str(round_number) + "\n"
        message += "Table: " + row[0] + "\n"

        whitePlayerDetails = get_player_details(row[2])
        message += "Joueur Blanc: " + row[2] + "\n"
        message += "Categorie: " + whitePlayerDetails[0] + "\n"
        message += "Club: " + whitePlayerDetails[1]
        if notification_players_ranking:
          message += "\n" + "Classement: " + row[3]

        blackPlayerDetails = get_player_details(row[5])
        message += "\n \nJoueur Noir: " + row[5] + "\n"
        message += "Categorie: " + blackPlayerDetails[0] + "\n"
        message += "Club: " + blackPlayerDetails[1]
        if notification_players_ranking:
          message += "\n" + "Classement: " + row[6]

        logger.info("Round found: \n" + message)
        return(message)

def tournament_name(tournament_id):
  url = "http://www.echecs.asso.fr/FicheTournoi.aspx?Ref=" + tournament_id
  results = check_url(url)
  table = table = results.find('table', id="ctl00_ContentPlaceHolderMain_TableTournoi") # Get Table results
  rows = []
  for i, row in enumerate(table.find_all('tr')):
    rows.append([el.text.strip() for el in row.find_all('td')])

  tournamentName = str(rows[0][0])
  logger.info("Tournament name: " + tournamentName)
  return(tournamentName)

log = "Starting program for tournament: " + tournament_id + " with " + round_total + " rounds for: " + player
log = "Starting on round: " + str(round_start)
logger.info(log)

tournamentName = tournament_name(tournament_id)
message = ""
message += "Notifications activées pour:\n" \
           "Nom du tournoi: " + tournamentName + "\n" \
           + "Joueur: " + player + "\n" \
           + "Nombre de rondes: " + round_total
logger.info(message)

push_over(message)


for rondeNumber in range(int(round_start), int(round_total)+1):
  message = ""
  logger.info("Round checked: " + str(rondeNumber))
  message += check_round(rondeNumber) + "\n\n"
  if rondeNumber-1 != 0:
    message += get_ranking(rondeNumber-1, "light")

  logger.info("Message with light Ranking is: \n" + message)
  push_over(message)

logger.info("Round: " + str(round_total) + " finished waiting for results")
message = get_ranking(round_total)
logger.info("Final message with Full Ranking is: \n" + message)
push_over(message)