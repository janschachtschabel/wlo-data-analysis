# Abschluss-Audit (15.11.2026)

Unabhängige Prüfung der gesamten App nach Paket 20: Sicherheitsgrenze, Anmeldung, Assistent,
Docker, Doku, Robustheit, Oberfläche. Ergebnis: kein kritischer Befund. Die Grenze hält: Es
gibt keinen Weg, auf dem Datensätze, Titel, Node-IDs, Zugangsdaten oder Schlüssel nach außen
gelangen.

## Behoben (Tests: `backend/tests/test_audit.py`, `backend/tests/test_compose.py`)

- **README veraltet:** Stand, Lesezugang, Routentabelle, Formate, Webhook-Felder,
  Voreinstellungen im Docker-Stack nachgezogen; der Design-Entwurf ist als historisch markiert.
- **Compose reichte elf Einstellungen nicht durch:** alle stehen jetzt in
  `docker-compose.yml` und `.env.example`; ein Test hält Compose, `.env.example` und die
  Einstellungen der App beisammen und prüft die Regeln für den Docker Manager von Hostinger.
- **Anmelde-Sperre:** zusätzlich eine Sperre über alle Adressen (30 Fehlversuche je Minute),
  falls ein Proxy `X-Forwarded-For` nicht säubert. Warnung bei kurzem Admin-Passwort.
- **Herkunft schreibender Anfragen:** Das Tor lehnt sie ab, wenn sie nicht von der eigenen
  Oberfläche kommen (`Sec-Fetch-Site`, sonst `Origin` gegen den Host).
- **Fehler der Ablage** (voller Datenträger, gesperrte Datei) antworten mit 503 und einem
  Satz; eine beschädigte Datenbank beendet den Start mit Meldung statt mit Neustart-Schleife.
- **Läufe gelöschter Definitionen** werden wie die übrigen auf `WDA_RUNS_KEEP` begrenzt.
- **`Strict-Transport-Security`** bei HTTPS-Cookies.
- **Lesezugang** sieht von Instanzen nur Kennung und Name und keinen Hinweis auf Aktionen,
  die er nicht hat.
- **Image** entsteht auf `main` erst nach grüner CI; der Stack zieht bei jedem Bereitstellen
  das aktuelle Image.

## Bewusst offen

- Abmelden entwertet das Cookie nicht auf dem Server (die Sitzung ist zustandslos); ein neues
  Admin-Passwort beendet alle Sitzungen. Steht im README.
- Die Datenbankdatei schrumpft nach dem Löschen nicht von selbst.
- Ob Traefik beim Hoster fremde `X-Forwarded-For` verwirft, zeigt erst der erste Einsatz; die
  zweite Sperre deckt den anderen Fall ab.
