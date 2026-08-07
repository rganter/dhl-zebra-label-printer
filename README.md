# DHL Zebra Label Printer

Eine kleine, plattformunabhängige Docker-Webanwendung, die die Versandmarke aus einseitigen A4-PDFs der DHL Online Frankierung extrahiert und per Zebra-ZPL direkt über TCP/9100 druckt.

## Funktionen

- Drag-and-drop-Upload und serverseitige HTML-Oberfläche
- konservative DHL-Erkennung über die Textanker `DHL Online Frankierung`, `Sendungsinformation` und `Ihre Unterlagen`
- automatische Erkennung der im PDF gedrehten Schreibrichtung
- vektorbasierter Beschnitt; Rasterung erst in der konfigurierten Druckerauflösung
- Vorschau, Download als 100 × 150-mm-PDF und direkter ZPL/GRF-Druck
- Profile für 203- und 300-dpi-Drucker
- Original-Uploads werden nicht auf Datenträger geschrieben; konvertierte Ergebnisse liegen höchstens 15 Minuten im Arbeitsspeicher

Unbekannte, mehrseitige oder geometrisch unplausible Layouts werden nicht gedruckt. Das MVP unterstützt ausschließlich DHL, ist aber in Verarbeitung, Konfiguration und Drucktransport getrennt aufgebaut, sodass später weitere Carrier-Profile ergänzt werden können.

## Schnellstart mit Docker Compose

```bash
cp config/printers.example.yml config/printers.yml
# IP-Adressen, DPI und Format in config/printers.yml anpassen
export PRINTER_CONFIG_FILE=./config/printers.yml  # PowerShell: $env:PRINTER_CONFIG_FILE="./config/printers.yml"
export WEB_PORT=8085                              # PowerShell: $env:WEB_PORT="8085"
docker compose up -d --build
```

Danach `http://SERVER-IP:8085` öffnen. Ohne `WEB_PORT` verwendet die Anwendung weiterhin Host-Port `8000`. Der Container muss die Drucker-IP auf TCP-Port 9100 erreichen können. CUPS wird nicht benötigt.

Die DPI muss zum konkreten Druckkopf passen. Besonders der ZD420 ist je nach Modell mit 203 oder 300 dpi erhältlich. Breite und Höhe werden in Millimetern konfiguriert und erst beim Druck in Druckpunkte umgerechnet.

## Portainer

1. Das Repository `https://github.com/rganter/dhl-zebra-label-printer` in Portainer als Git-Stack anlegen.
2. Eine Druckerkonfiguration auf dem Docker-Host hinterlegen und die Stack-Umgebungsvariable `PRINTER_CONFIG_FILE` auf diesen Pfad setzen. Ohne Variable startet der Stack mit `config/printers.example.yml`; deren Beispiel-IP-Adressen müssen vor einem echten Druck ersetzt werden.
3. Optional `WEB_PORT` auf einen freien Host-Port setzen, beispielsweise `8085`. Ohne diese Variable wird `8000` verwendet.
4. Den Stack deployen und den gewählten Host-Port freigeben.
5. `http://SERVER-IP:WEB_PORT/health` sollte `{"status":"ok"}` liefern.
6. Zuerst Vorschau und PDF-Download prüfen, anschließend ein Testlabel drucken.

### Umgebungsvariablen

| Variable | Standard | Bedeutung |
| --- | --- | --- |
| `WEB_PORT` | `8000` | Auf dem Docker-Host veröffentlichter HTTP-Port. Der interne Container-Port bleibt `8000`. |
| `PRINTER_CONFIG_FILE` | `./config/printers.example.yml` | Pfad der Druckerkonfiguration auf dem Docker-Host. |
| `PRINTER_CONFIG` | `/config/printers.yml` | Interner Konfigurationspfad im Container; normalerweise nicht zu ändern. |

Der Compose-Stack läuft mit schreibgeschütztem Dateisystem, ohne zusätzliche Privilegien und nur einem Uvicorn-Worker. Ein Worker ist erforderlich, weil die kurzlebigen Ergebnisse im Prozessspeicher liegen.

## Lokale Entwicklung

```bash
python -m venv .venv
. .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config/printers.example.yml config/printers.yml
uvicorn app.main:app --reload
pytest
```

## Druckdaten

Das extrahierte Label bleibt bis zum letzten Schritt PDF/Vektor. Für das gewählte Profil wird es genau einmal auf die Zieldimension gerendert, monochromisiert und als `^GFA`-Grafik in einem ZPL-Job an TCP/9100 gesendet. Das Seitenverhältnis bleibt erhalten; nicht belegte Fläche wird weiß aufgefüllt.

## Datenschutz

- Keine Upload-Dateinamen, Adressen, Sendungsnummern oder PDF-Inhalte werden geloggt.
- Original-PDFs werden nur als Request-Bytes verarbeitet und danach verworfen.
- Vorschau und konvertiertes PDF werden im RAM gehalten und nach Druck oder spätestens 15 Minuten entfernt.
- Echte PDFs sind per `.gitignore` und `.dockerignore` ausgeschlossen. Test-Fixtures müssen synthetisch oder anonymisiert sein.

Die Anwendung besitzt im MVP keine Authentifizierung. Sie sollte ausschließlich in einem vertrauenswürdigen LAN oder hinter einem authentifizierenden Reverse Proxy betrieben werden.

## Grenzen des MVP

- einseitige DHL-Online-Frankierungs-PDFs mit den bekannten Textankern
- In-Memory-Speicher, daher genau ein App-Worker und keine horizontale Skalierung
- keine Druckerstatusabfrage; ein erfolgreicher TCP-Versand bestätigt nicht die mechanische Ausgabe
- noch keine Carrier Hermes, DPD oder GLS

## Lizenz

MIT
