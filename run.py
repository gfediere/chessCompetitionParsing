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

def check_url(ronde):
   url = "http://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/"+tournamendID+"/"+tournamendID+"&Action=0"+str(ronde)
   logger.info("URL checked: "+ url)
   soup = BeautifulSoup(requests.get(url).content, 'html.parser')
   return soup
      

def check_ronde(ronde):
  result = check_url(ronde)
  while not (re.findall(str(player), str(result.contents))):
    logger.info("Page non a jour pour ronde: " + str(ronde))
    time.sleep(60)
    check_url(ronde)
  #if re.findall(str(player), str(soup.contents)):
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
          message = "Ronde: " + str(ronde) + "\n" + "Table: " + row[0]+ "\n" + "Joueur Noir: " + row[2] + "\n" + "Classement: " + row[3] + "\n"  \
            + "Joueur blanc: " + row[5] + "\n" + "Classement: " + row[6]

          logger.info("Partie trouvée: \n" + message)
          pushOver(message)
          return(message)

logger.info("Demarrage du programme pour le tournoi: " + tournamendID + " avec " + roundTotal + " ronde pour : " + player)
for i in range(1, int(roundTotal)+1):
  logger.info("Ronde checked: " + str(i))
  check_ronde(i)
