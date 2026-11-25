# EV Charge Director

Home Assistant-integratie voor elke laadpaal: houdt laadsessies en kosten bij, laadt slim op de goedkoopste momenten zodat de auto op tijd klaar is, en levert een eigen dashboardkaart mee.

Je zit niet vast aan één merk laadpaal of energieleverancier. Je koppelt zelf welke sensoren, knoppen en statuswaarden er bij jouw installatie horen.

## Wat het doet

- **Laadsessies**: start en einde worden herkend aan de status van de laadpaal. kWh en kosten worden live bijgehouden, ook als de energiemeter tussendoor naar 0 springt (een dagteller). Zonder energiemeter wordt de energie berekend uit het vermogen.
- **Slim laden**: stel in wanneer de auto klaar moet zijn en hoeveel er bij moet (in kWh, of in % als de auto een SoC-sensor heeft). De integratie kiest de goedkoopste blokken voor die deadline en start of stopt de laadpaal daarop. Komt de deadline te dichtbij, of is er geen prijsdata, dan laadt hij gewoon direct.
- **Handmatig ingrijpen**: met *Start* laad je direct door, met *Stop* pauzeer je. *Planning hervatten* geeft de beslissing terug aan de planner.
- **CSV-log per auto en maand**: hetzelfde formaat als het oude package, dus bestaande bestanden tellen gewoon mee.
- **Maandrapport**: een melding per auto op een vaste dag, plus een knop of service om hem direct te versturen.
- **Kaart**: status, vermogen, sessie, planning met prijsgrafiek, historie en maandoverzicht.
- Alles wordt opgeslagen in Home Assistant, dus een herstart halverwege een sessie is geen probleem.

## Installatie via HACS

1. Ga in HACS naar ⋮ → **Custom repositories**, voeg `https://github.com/lucasmunster06-ship-it/EVchargedirector` toe en kies type **Integration**.
2. Installeer **EV Charge Director** en herstart Home Assistant.
3. Ga naar **Instellingen → Apparaten & diensten → Integratie toevoegen → EV Charge Director**.
4. Voeg de kaart toe aan een dashboard: zoek in de kaartkiezer op *EV Charge Director*, of gebruik:

   ```yaml
   type: custom:ev-charge-director-card
   ```

   De kaart wordt automatisch geladen. Je hoeft geen resource toe te voegen en button-card is niet nodig. Zet de weergave op **Paneel** voor het brede overzicht met drie kolommen; op een telefoon staat alles onder elkaar.

## Instellen

Alles is later aan te passen via **Configureren** bij de integratie.

| Onderdeel | Wat je koppelt |
|---|---|
| Bronnen | Status (verplicht), energiemeter, vermogen, actuele prijs, optioneel een aparte prijsvoorspelling |
| Bediening | Start (knop, schakelaar of script), stop, en optioneel *mag starten* / *mag stoppen* |
| Statuswaarden | Welke ruwe waarde welke betekenis heeft, en bij welke status een sessie afrondt |
| Auto's | Naam, notify-dienst, SoC-sensor en accucapaciteit |
| Slim laden | Laadvermogen voor de planning en de veiligheidsmarge voor de deadline |
| Opslag | CSV aan/uit, de map, en dag en uur van het maandrapport |

Eenheden (W/kW, Wh/kWh, ct/€) worden automatisch omgerekend.

### Statuswaarden

Eén regel per waarde: `waarde | categorie | label`.

Categorieën: `disconnected`, `connected`, `charging`, `paused`, `finished`, `error`. Tekststatussen werken ook, bijvoorbeeld `Charging | charging | Laden`.

### Auto's

Eén auto per regel: `Naam | notify-dienst | SoC-sensor | accu kWh | extra entiteiten`. Alleen de naam is verplicht; een lege kolom schrijf je als `| |`. De extra entiteiten (komma-gescheiden, bijv. bereik, laadstatus, locatie) toont de kaart in het autovak; klik erop voor meer info.

### Prijsvoorspelling

De integratie zoekt de prijzen zelf op in de attributen van de prijssensor (of van de voorspellingssensor die je koppelt). Herkend worden onder andere Frank Energie (`prices: [{from, till, price}]`), Nordpool (`raw_today`/`raw_tomorrow`), ENTSO-e, en templates met `{start, end, value}` of `{tijd: prijs}`. Blokken van een uur en van een kwartier werken allebei.

## Voorbeeld: Alpha ESS + Frank Energie (het oude package)

**Bronnen**
- Status: `sensor.alpha_ess_charger_alp231201538112_ald071025020864_ev_charger_status_raw`
- Energiemeter: `sensor.alpha_ess_energy_statistics_ald071025020864_ald071025020864_ev_charger`
- Vermogen: `sensor.alpha_ess_energy_statistics_ald071025020864_ald071025020864_pev`
- Prijs: `sensor.algemeen_frank_energie_stroomprijzen_huidige_elektriciteitsprijs_all_in`

**Bediening**
- Start: `button.alpha_ess_charger_alp231201538112_ald071025020864_start_charging`
- Stop: `button.alpha_ess_charger_alp231201538112_ald071025020864_stop_charging`
- Mag starten: `binary_sensor.alpha_ess_charger_alp231201538112_ald071025020864_can_start_charging`
- Mag stoppen: `binary_sensor.alpha_ess_charger_alp231201538112_ald071025020864_can_stop_charging`

**Statuswaarden** (standaard al ingevuld)

```
1 | disconnected | Klaar om op te laden
2 | connected | Opladen starten
3 | charging | Opladen
4 | paused | Opladen gepauzeerd
5 | paused | Opladen gepauzeerd door auto
6 | finished | Opladen voltooid
9 | error | Laadpaal storing
```

**Auto's**

```
Tesla | notify.lucas
Volvo | notify.richard
Gast
```

**Opslag**: zet de map op `www/laadpaal` om de bestaande CSV's te blijven gebruiken. Let op: alles onder `www` is zonder inloggen op te vragen via `/local/`. Wil je dat niet, verplaats de map dan naar bijvoorbeeld `laadpaal` (de standaard).

Daarna kun je het oude package (`evcharger.yaml`, `maandrapport_evcharger.yaml`) en de button-card-kaart weghalen.

## Services

| Service | Doel |
|---|---|
| `ev_charge_director.start_charging` | Direct starten (met slim laden aan: doorladen tot het einde van de sessie) |
| `ev_charge_director.stop_charging` | Direct stoppen (met slim laden aan: pauzeren tot je de planning hervat) |
| `ev_charge_director.clear_override` | Planning hervatten |
| `ev_charge_director.set_smart_charging` | `enabled`, `ready_time`, `target_kwh`, `target_soc` |
| `ev_charge_director.select_car` / `next_car` | Auto kiezen |
| `ev_charge_director.send_monthly_report` | Maandrapport nu versturen (`car`, `month` optioneel) |

Bij meer dan één laadpunt geef je `config_entry_id` mee.

## Entiteiten

Per laadpunt krijg je de volgende entiteiten:

- **Sensoren**: status, vermogen, prijs, sessie-energie, -kosten en -start, de laatste sessie (energie, kosten, duur, gemiddelde prijs), planning (status, volgende start, verwachte kosten, nog nodig) en per auto *vorige maand*
- **Overig**: de schakelaar *Slim laden*, het tijdstip *Klaar om*, de laaddoelen in kWh en %, de keuze van de auto, en knoppen voor start, stop, volgende auto, planning hervatten en het maandrapport

Daarmee kun je ook eigen automatiseringen of kaarten bouwen.
