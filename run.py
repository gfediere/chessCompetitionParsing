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

APP_TOKEN = os.environ['pushover_app_token']
USER_KEY = os.environ['pushover_user_key']

tournamendID = os.environ['tournament_id']
roundTotal = os.environ['rondes']
player = os.environ['user']

def pushOver(message):
  if not "dry-run" in os.environ:
    conn = http.client.HTTPSConnection("api.pushover.net:443")
    conn.request("POST", "/1/messages.json",
      urllib.parse.urlencode({
       "token": APP_TOKEN,
       "user": USER_KEY,
       "message": message,
     }), { "Content-type": "application/x-www-form-urlencoded" })
    response=conn.getresponse()
    if response.status != 200:
      logger.error("PushOver issue, error returned: " + str(response.status) + "log: " + response.reason)
    else:
      logger.info("PushOver message sent !")
  else:
    logger.info("Dry run program PushOver not sent !")

def check_url(url):
   logger.info("URL checked: "+ url)
   soup = BeautifulSoup(requests.get(url).content, 'html.parser')
   logger.debug("Page content: " + str(soup.contents))
   return soup

def get_ranking(ronde, type="full"):
  logger.info("Ranking type is: " + type)
  url = "http://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/"+tournamendID+"/"+tournamendID+"&Action=Cl"
  if type == "full":
    while True:
      result = check_url(url)
      parsedContent = "après la ronde " + str(ronde) # Test if ranking page up-to-date regarding ronde
      if re.findall(str(parsedContent), str(result.contents)):
        logger.info("Content: " + parsedContent + " found")
        break
      else:
        logger.info("Content: " + parsedContent + " NOT found")
        time.sleep(60)
        continue
  result = check_url(url)
  logger.info("Page a jour pour Resultats")
  table = result.find('table', attrs = {'id':'TablePage'}) # Get Table results
  rows = []
  for i, row in enumerate(table.find_all('tr')):
    if i == 0:
      header = [el.text.strip() for el in row.find_all('th')]
    else:
      rows.append([el.text.strip() for el in row.find_all('td')])

  logger.info("Getting results for: " + player)   
  categorie = ""
  message = ""   
  for cell in rows:
    if player in cell:
      points = cell[8]
      categorie = cell[4]
      classementGeneral = cell[0]

      logger.info("Player: " + player + " FOUND in page: " + url)
      message = "Resultats pour " + player + " apres la ronde " + str(ronde) + "\n"

      if type == "light":
        message += "Classement General: " + classementGeneral + "\n" \
        + "Points: " + points + "\n"

      else: # full ranking
        message += "Classement General: " + classementGeneral + "\n" \
        + "Catégorie: " + categorie + "\n" \
        + "Points: " + points + "\n\n"

  logger.info("Getting results for: " + player + " in category: " + categorie)
  message += "Classement pour categorie " + categorie + "\n"
  rowNumber = 0
  for cell in rows:
      if categorie in cell:
        rowNumber += 1
        if type == "light":
          message += str(rowNumber) + "-" + cell[2] + " / Points: " + cell[8] + "\n"

        else:
          message += str(rowNumber) + "-" + cell[2] + "\n" \
          + "Classement General: " + cell[0] + "\n" \
          + "Club: " + cell[7] + "\n" \
          + "Points " + cell[8] + "\n" \
          + "Perf " + cell[10] + "\n\n"
  
  logger.info(message)
  return(message)

def check_ronde(ronde):
  while True:
    logger.debug("Appel fonction check URL pour ronde: " + str(ronde))
    url = "http://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/"+tournamendID+"/"+tournamendID+"&Action=0"+str(ronde)
    result = check_url(url)
    logger.debug("Page returned in while loop: " + str(result.contents))
    if re.findall(str(player), str(result.contents)):
      logger.debug("Break")
      break
    else:
      logger.info("Page non a jour pour ronde: " + str(ronde))
      time.sleep(20) # Waiting time between URL checks
      continue

  result = check_url(url)
  logger.info("Page a jour pour ronde: " + str(ronde))

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
        message = "Ronde: " + str(ronde) + "\n" \
        + "Table: " + row[0]+ "\n" \
        + "Joueur Blanc: " + row[2] + "\n" \
        + "Classement: " + row[3] + "\n"  \
        + "Joueur Noir: " + row[5] + "\n" \
        + "Classement: " + row[6]

        logger.info("Partie trouvée: \n" + message)
        return(message)

logger.info("Demarrage du programme pour le tournoi: " + tournamendID + " avec " + roundTotal + " ronde pour : " + player)
for rondeNumber in range(1, int(roundTotal)+1):
  message = ""
  logger.info("Ronde checked: " + str(rondeNumber))
  message += check_ronde(rondeNumber) + "\n\n"
  message += get_ranking(rondeNumber, "light")
  logger.info("Message est: \n" + message)
  pushOver(message)

logger.info("Ronde: " + str(roundTotal) + " finished waiting for results")
get_ranking(roundTotal)