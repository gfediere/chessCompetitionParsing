# chessCompetitionParsing
## Description

This project is a web scraper for the French Chess website: http://www.echecs.asso.fr/. Many chess competitions are relying on this website to provide datas about tournaments statistics, results ...
The goal of this project is to provide a way to scrape for a given competition and a player, all chess rounds and send [PushOver](https://pushover.net/) notifications as soon as results/rounds are online/ready to play. In this way players doesn't have to refresh the webpage to get their datas

## Usage

The application takes environment variables (tournamend id, player name, ...). A given file config is provided in the `template.config.tpl` and should look like this:

```
tournament_id=666 # Tournamend ID http://www.echecs.asso.fr/FicheTournoi.aspx?Ref=666
rounds=4 # Total number of rounds for the given tournament
user=DOE John # Player Name case sensitive
pushover_app_token=friufriubrf # PushOver token
pushover_user_key=rfughrfygrf # PushOver user key
dry-run=True # if mentioned, the program won't send any notification (only dry run)
no-notification-players-ranking=True # If mentioned, opponent ELO won't be sent on table pairings
round_start=2 # Optional to start from a round number, not first one
```
