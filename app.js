/* PrijsProfeet status page — renders data/status.json, written by
 * collector/probe.py. No framework, no build, no network calls off this
 * origin: during the outages this page exists to report, anything it
 * fetched from elsewhere would be one more thing that can fail.
 *
 * Two audiences on one page. A visitor picks a storefront (Nederland,
 * België) and sees only that one: its website, the connector in Claude and
 * ChatGPT, the freshness of its chains and its incidents. API customers get a
 * block below with the API and Business SLA of that storefront's host, and a
 * link to the other hosts.
 */

const WINDOW_DAYS = 31;

// The collector commits new data roughly every 5 minutes; polling faster
// than that only spends the visitor's battery and GitHub Pages' bandwidth
// on refetching a file that hasn't changed. Polling slower would mean a
// visitor who leaves the tab open during an outage sits on a stale "Alles werkt"
// for longer than the collector itself needed.
const POLL_INTERVAL_MS = 60_000;
// The "Bijgewerkt N minuten geleden" line goes stale even between polls; refreshing
// just that text needs no network call at all.
const CLOCK_TICK_MS = 15_000;
// The collector runs every 5 minutes, and GitHub can delay a scheduled run.
// Past this age the data no longer says anything about now, so the banner
// must stop reporting a status: a stalled collector would otherwise leave the
// page green through the very outage it should show.
const STALE_AFTER_MINUTES = 20;

// The storefronts, and the languages each one speaks. België speaks Dutch and
// French, like its storefront (prijsprofeet#1568), and German (prijsprofeet#1697); English is there for API
// customers on both (prijsprofeet#1670). A language listed here needs a STRINGS
// table, and the language switch appears on its own.
const COUNTRIES = ['nl', 'be'];
const LANGS = { nl: ['nl', 'en'], be: ['nl', 'fr', 'de', 'en'] };
// Never picked from the browser: plenty of Dutch and Belgian visitors run an
// English browser and read the consumer site in Dutch. English comes from the
// switch or from a link (?taal=en, which the English API pages use).
const NOT_FROM_BROWSER = ['en'];
// An alert carries a Dutch, French and English title (prijsprofeet alerts.yml).
// German pages fall back to French, like the German storefront reads the French
// product names: in East Belgium French is read before Dutch (prijsprofeet#1697).
const TITLE_FALLBACK = { de: 'fr' };
const HOSTS = { nl: 'www.prijsprofeet.nl', be: 'www.prijsprofeet.be' };
// Rows shown to visitors, in this order. The API rows sit in the business block.
const VISITOR_CHECKS = ['website', 'mcp'];
const STORAGE_KEY = 'pp_status_choice';

/* Every sentence on the page. Dutch is the source; a second language is a
 * second table with the same keys (a missing key falls back to Dutch). The
 * reason codes are the ones probe.py stores (its REASON_TEXT, the feed's
 * Dutch, must name the same set: test_probe.py checks both). */
const STRINGS = {
  nl: {
    title: 'PrijsProfeet status',
    loading: 'Status laden…',
    choose_country: 'Kies je land',
    choose_lang: 'Kies je taal',
    country_nl: 'Nederland',
    country_be: 'België',
    lang_nl: 'Nederlands',
    lang_fr: 'Français',
    lang_de: 'Deutsch',
    lang_en: 'English',
    updated: 'Bijgewerkt {ago}',
    last_check: 'Laatste check: {when}',
    load_failed: 'Statusgegevens konden niet worden geladen',
    banner: { up: 'Alles werkt', degraded: 'Gedeeltelijke storing', down: 'Storing', none: 'Geen gegevens' },
    banner_stale: 'Gegevens verouderd: de laatste check was {ago}',
    status: { up: 'In orde', degraded: 'Verstoord', down: 'Storing', none: 'Geen gegevens' },
    no_recent_check: 'Geen recente check',
    checked: 'Gecontroleerd {ago}',
    check_website: 'Website',
    check_mcp: 'In Claude en ChatGPT',
    check_api: 'API',
    reason: {
      ok: '200 OK',
      cloudflare: 'bereikbaar (Cloudflare houdt geautomatiseerde checks hier bewust tegen)',
      timeout: 'onbereikbaar: time-out',
      reset: 'onbereikbaar: verbinding verbroken',
      refused: 'onbereikbaar: verbinding geweigerd',
      incomplete: 'onbereikbaar: antwoord onvolledig',
      dns: 'onbereikbaar: DNS-fout',
      tls: 'onbereikbaar: TLS-fout',
      unreachable: 'onbereikbaar',
      mcp_invalid: 'geen geldig antwoord van de connector',
      mcp_tools: 'de connector mist een tool (search of fetch)',
      not_fresh: 'data van vannacht niet bijgewerkt om 07:00',
      http: 'HTTP {status}',
    },
    ago: {
      never: 'nooit',
      unknown: 'onbekend',
      now: 'zojuist',
      minutes: (n) => (n === 1 ? '1 minuut geleden' : `${n} minuten geleden`),
      hours: (n) => (n === 1 ? '1 uur geleden' : `${n} uur geleden`),
      days: (n) => `${n} dagen geleden`,
    },
    duration: {
      under_minute: 'minder dan een minuut',
      minutes: '{n} min',
      hours: '{h} u',
      hours_minutes: '{h} u {m} min',
      days: (n) => (n === 1 ? '1 dag' : `${n} dagen`),
      days_hours: '{days} {h} u',
    },
    day_not_measured: 'Nog niet gemeten',
    day_no_checks: 'Geen checks vastgelegd',
    day_all_ok: '{runs} checks, alle geslaagd',
    day_failed: '{failed} van {runs} checks mislukt',
    day_no_incident: (n) => (n === 1 ? 'losse check, geen incident' : 'losse checks, geen incident'),
    incidents_title: 'Incidenten',
    incidents_note:
      'Geopend na twee opeenvolgende mislukte checks (~10 min), zodat een enkele time-out geen "incident" is. ' +
      'Dekt wat deze probe zelf ziet, plus de storingen die onze eigen monitoring publiek meldt.',
    ongoing: 'Lopend',
    since: 'sinds {start}',
    lasted: '{start}, duurde {duration}',
    not_fresh_incident: '{chain}: niet vers',
    notice_meta: 'Geplaatst {published}',
    notice_until: ' · geldt tot {until}',
    freshness_title: 'Versheid per keten',
    freshness_note:
      'Een keten is vers als de data van die nacht om 07:00 binnen is: de acties ververst en, waar de keten ze ' +
      'levert, de schapprijzen en barcodes op peil. Elke ochtend om 07:05 vastgelegd en daarna niet meer ' +
      'aangepast. De norm is 90% verse nachten per keten per kalendermaand.',
    fresh_this_morning: 'Vanochtend ({day})',
    fresh_on: 'Op {day}',
    fresh_lead: '{when}: {fresh} van {total} ketens vers.',
    fresh_not: ' Niet vers: {names}.',
    fresh_unmeasured: ' Nog geen meting: {names}.',
    fresh_details: 'Per keten en per maand',
    col_chain: 'Keten',
    col_at_seven: 'Om 07:00',
    running: ' (loopt nog)',
    not_measured: 'nog niet gemeten',
    fresh: 'vers',
    not_fresh: 'niet vers',
    business_title: 'Voor API-klanten',
    business_note:
      'De API en de Business SLA van dit land. Eén key werkt op elke host; de host bepaalt het land van de data.',
    other_country: 'Gebruik je de API van een ander land? Kies dat land bovenaan de pagina.',
    sla_title: 'Maandelijkse beschikbaarheid (Business SLA)',
    sla_note:
      'Dit is de contractuele meting: elke 30 seconden een check op /api/v1/ready van elke host, via dezelfde ' +
      'publieke route als een bezoeker (Cloudflare en nginx). Releases en onderhoud tellen gewoon mee; er wordt ' +
      'niets uitgesloten. De norm van 99,5% geldt per host die je gebruikt en wordt per host apart gemeten, nooit ' +
      'als gemiddelde. Zie de',
    sla_terms: 'API-voorwaarden (art. 10)',
    col_month: 'Maand',
    col_availability: 'Beschikbaarheid',
    col_downtime: 'Uitval',
    col_target: 'Doel: {pct}%',
    downtime_none: 'geen',
    sla_running: 'loopt nog',
    sla_met: 'gehaald',
    sla_missed: 'gemist',
    measured_from: '{month} (gemeten vanaf {day})',
    footer:
      'De checks hierboven lopen elke paar minuten vanaf GitHub Actions, buiten het netwerk van PrijsProfeet, ' +
      'zodat een storing daar deze pagina niet meesleurt. Elke balk is één dag (Nederlandse en Belgische tijd) ' +
      'over de laatste 31 dagen: groen is alles geslaagd, oranje een dag met een incident, rood een dag waarop ' +
      'elke check mislukte. Een omlijnde balk valt vóór de start van die host. Storingen en mededelingen volgen? ' +
      'Abonneer je op de',
    footer_feed: 'Atom-feed',
  },
  fr: {
    title: 'Statut de PrijsProfeet',
    loading: 'Chargement du statut…',
    choose_country: 'Choisissez votre pays',
    choose_lang: 'Choisissez votre langue',
    country_nl: 'Pays-Bas',
    country_be: 'Belgique',
    lang_nl: 'Nederlands',
    lang_fr: 'Français',
    lang_de: 'Deutsch',
    lang_en: 'English',
    updated: 'Mis à jour {ago}',
    last_check: 'Dernière vérification : {when}',
    load_failed: 'Impossible de charger les données de statut',
    banner: { up: 'Tout fonctionne', degraded: 'Panne partielle', down: 'Panne', none: 'Aucune donnée' },
    banner_stale: 'Données obsolètes : la dernière vérification date de {ago}',
    status: { up: 'Opérationnel', degraded: 'Perturbé', down: 'Panne', none: 'Aucune donnée' },
    no_recent_check: 'Aucune vérification récente',
    checked: 'Vérifié {ago}',
    check_website: 'Site web',
    check_mcp: 'Dans Claude et ChatGPT',
    check_api: 'API',
    reason: {
      ok: '200 OK',
      cloudflare: 'accessible (Cloudflare bloque volontairement les vérifications automatiques ici)',
      timeout: 'inaccessible : délai dépassé',
      reset: 'inaccessible : connexion interrompue',
      refused: 'inaccessible : connexion refusée',
      incomplete: 'inaccessible : réponse incomplète',
      dns: 'inaccessible : erreur DNS',
      tls: 'inaccessible : erreur TLS',
      unreachable: 'inaccessible',
      mcp_invalid: 'pas de réponse valide du connecteur',
      mcp_tools: 'il manque un outil au connecteur (search ou fetch)',
      not_fresh: 'données de la nuit pas à jour à 07h00',
      http: 'HTTP {status}',
    },
    ago: {
      never: 'jamais',
      unknown: 'inconnu',
      now: 'à l’instant',
      minutes: (n) => (n === 1 ? 'il y a 1 minute' : `il y a ${n} minutes`),
      hours: (n) => (n === 1 ? 'il y a 1 heure' : `il y a ${n} heures`),
      days: (n) => `il y a ${n} jours`,
    },
    duration: {
      under_minute: 'moins d’une minute',
      minutes: '{n} min',
      hours: '{h} h',
      hours_minutes: '{h} h {m} min',
      days: (n) => (n === 1 ? '1 jour' : `${n} jours`),
      days_hours: '{days} {h} h',
    },
    day_not_measured: 'Pas encore mesuré',
    day_no_checks: 'Aucune vérification enregistrée',
    day_all_ok: '{runs} vérifications, toutes réussies',
    day_failed: '{failed} vérifications sur {runs} échouées',
    day_no_incident: (n) => (n === 1 ? 'vérification isolée, pas d’incident' : 'vérifications isolées, pas d’incident'),
    incidents_title: 'Incidents',
    incidents_note:
      'Ouvert après deux vérifications échouées d’affilée (~10 min), pour qu’un simple délai dépassé ne soit pas un « incident ». ' +
      'Couvre ce que cette sonde voit elle-même, plus les pannes que notre propre surveillance signale publiquement.',
    ongoing: 'En cours',
    since: 'depuis {start}',
    lasted: '{start}, a duré {duration}',
    not_fresh_incident: '{chain} : pas à jour',
    notice_meta: 'Publié {published}',
    notice_until: ' · valable jusqu’au {until}',
    freshness_title: 'Fraîcheur par chaîne',
    freshness_note:
      'Une chaîne est à jour si les données de la nuit sont là à 07h00 : les promotions actualisées et, quand la ' +
      'chaîne les fournit, les prix en rayon et les codes-barres au niveau habituel. Enregistré chaque matin à ' +
      '07h05, puis plus modifié. La norme est de 90 % de nuits à jour par chaîne et par mois civil.',
    fresh_this_morning: 'Ce matin ({day})',
    fresh_on: 'Le {day}',
    fresh_lead: '{when} : {fresh} chaînes sur {total} à jour.',
    fresh_not: ' Pas à jour : {names}.',
    fresh_unmeasured: ' Pas encore de mesure : {names}.',
    fresh_details: 'Par chaîne et par mois',
    col_chain: 'Chaîne',
    col_at_seven: 'À 07h00',
    running: ' (en cours)',
    not_measured: 'pas encore mesuré',
    fresh: 'à jour',
    not_fresh: 'pas à jour',
    business_title: 'Pour les clients de l’API',
    business_note:
      'L’API et le SLA Business de ce pays. Une clé fonctionne sur chaque hôte ; l’hôte détermine le pays des données.',
    other_country: 'Vous utilisez l’API d’un autre pays ? Choisissez ce pays en haut de la page.',
    sla_title: 'Disponibilité mensuelle (SLA Business)',
    sla_note:
      'C’est la mesure contractuelle : toutes les 30 secondes, une vérification de /api/v1/ready sur chaque hôte, ' +
      'par la même route publique qu’un visiteur (Cloudflare et nginx). Les mises en production et la maintenance ' +
      'comptent normalement ; rien n’est exclu. La norme de 99,5 % vaut par hôte utilisé et est mesurée par hôte, ' +
      'jamais en moyenne. Voir les',
    sla_terms: 'conditions de l’API (art. 10, en néerlandais)',
    col_month: 'Mois',
    col_availability: 'Disponibilité',
    col_downtime: 'Indisponibilité',
    col_target: 'Objectif : {pct} %',
    downtime_none: 'aucune',
    sla_running: 'en cours',
    sla_met: 'atteint',
    sla_missed: 'manqué',
    measured_from: '{month} (mesuré à partir du {day})',
    footer:
      'Les vérifications ci-dessus tournent toutes les quelques minutes depuis GitHub Actions, en dehors du réseau ' +
      'de PrijsProfeet, pour qu’une panne là-bas n’entraîne pas cette page. Chaque barre est un jour (heure ' +
      'néerlandaise et belge) sur les 31 derniers jours : vert, tout a réussi ; orange, un jour avec un incident ; ' +
      'rouge, un jour où chaque vérification a échoué. Une barre en contour précède le lancement de cet hôte. ' +
      'Suivre les pannes et les annonces ? Abonnez-vous au',
    footer_feed: 'flux Atom (en néerlandais)',
  },
  de: {
    title: 'PrijsProfeet Status',
    loading: 'Status wird geladen…',
    choose_country: 'Wählen Sie Ihr Land',
    choose_lang: 'Wählen Sie Ihre Sprache',
    country_nl: 'Niederlande',
    country_be: 'Belgien',
    lang_nl: 'Nederlands',
    lang_fr: 'Français',
    lang_de: 'Deutsch',
    lang_en: 'English',
    updated: 'Aktualisiert {ago}',
    last_check: 'Letzte Prüfung: {when}',
    load_failed: 'Statusdaten konnten nicht geladen werden',
    banner: { up: 'Alles funktioniert', degraded: 'Teilweise Störung', down: 'Störung', none: 'Keine Daten' },
    banner_stale: 'Veraltete Daten: die letzte Prüfung war {ago}',
    status: { up: 'In Ordnung', degraded: 'Beeinträchtigt', down: 'Störung', none: 'Keine Daten' },
    no_recent_check: 'Keine aktuelle Prüfung',
    checked: 'Geprüft {ago}',
    check_website: 'Website',
    check_mcp: 'In Claude und ChatGPT',
    check_api: 'API',
    reason: {
      ok: '200 OK',
      cloudflare: 'erreichbar (Cloudflare blockiert automatische Prüfungen hier absichtlich)',
      timeout: 'nicht erreichbar: Zeitüberschreitung',
      reset: 'nicht erreichbar: Verbindung abgebrochen',
      refused: 'nicht erreichbar: Verbindung abgelehnt',
      incomplete: 'nicht erreichbar: unvollständige Antwort',
      dns: 'nicht erreichbar: DNS-Fehler',
      tls: 'nicht erreichbar: TLS-Fehler',
      unreachable: 'nicht erreichbar',
      mcp_invalid: 'keine gültige Antwort vom Connector',
      mcp_tools: 'dem Connector fehlt ein Tool (search oder fetch)',
      not_fresh: 'Daten der Nacht um 07:00 Uhr nicht aktuell',
      http: 'HTTP {status}',
    },
    ago: {
      never: 'nie',
      unknown: 'unbekannt',
      now: 'gerade eben',
      minutes: (n) => (n === 1 ? 'vor 1 Minute' : `vor ${n} Minuten`),
      hours: (n) => (n === 1 ? 'vor 1 Stunde' : `vor ${n} Stunden`),
      days: (n) => `vor ${n} Tagen`,
    },
    duration: {
      under_minute: 'weniger als eine Minute',
      minutes: '{n} Min.',
      hours: '{h} Std.',
      hours_minutes: '{h} Std. {m} Min.',
      days: (n) => (n === 1 ? '1 Tag' : `${n} Tage`),
      days_hours: '{days} {h} Std.',
    },
    day_not_measured: 'Noch nicht gemessen',
    day_no_checks: 'Keine Prüfungen erfasst',
    day_all_ok: '{runs} Prüfungen, alle erfolgreich',
    day_failed: '{failed} von {runs} Prüfungen fehlgeschlagen',
    day_no_incident: (n) => (n === 1 ? 'einzelne Prüfung, kein Vorfall' : 'einzelne Prüfungen, kein Vorfall'),
    incidents_title: 'Vorfälle',
    incidents_note:
      'Eröffnet nach zwei fehlgeschlagenen Prüfungen in Folge (~10 Min.), damit eine einzelne Zeitüberschreitung ' +
      'kein „Vorfall“ ist. Umfasst, was diese Prüfung selbst sieht, sowie die Störungen, die unsere eigene ' +
      'Überwachung öffentlich meldet.',
    ongoing: 'Läuft',
    since: 'seit {start}',
    lasted: '{start}, Dauer {duration}',
    not_fresh_incident: '{chain}: nicht aktuell',
    notice_meta: 'Veröffentlicht {published}',
    notice_until: ' · gültig bis {until}',
    freshness_title: 'Aktualität pro Kette',
    freshness_note:
      'Eine Kette ist aktuell, wenn die Daten der Nacht um 07:00 Uhr da sind: die Angebote erneuert und, wo die ' +
      'Kette sie liefert, die Regalpreise und Barcodes auf dem üblichen Stand. Jeden Morgen um 07:05 Uhr ' +
      'festgehalten und danach nicht mehr geändert. Die Norm sind 90 % aktuelle Nächte pro Kette und Kalendermonat.',
    fresh_this_morning: 'Heute Morgen ({day})',
    fresh_on: 'Am {day}',
    fresh_lead: '{when}: {fresh} von {total} Ketten aktuell.',
    fresh_not: ' Nicht aktuell: {names}.',
    fresh_unmeasured: ' Noch keine Messung: {names}.',
    fresh_details: 'Pro Kette und pro Monat',
    col_chain: 'Kette',
    col_at_seven: 'Um 07:00',
    running: ' (läuft noch)',
    not_measured: 'noch nicht gemessen',
    fresh: 'aktuell',
    not_fresh: 'nicht aktuell',
    business_title: 'Für API-Kunden',
    business_note:
      'Die API und das Business-SLA dieses Landes. Ein Key funktioniert auf jedem Host; der Host bestimmt das Land der Daten.',
    other_country: 'Sie nutzen die API eines anderen Landes? Wählen Sie dieses Land oben auf der Seite.',
    sla_title: 'Monatliche Verfügbarkeit (Business-SLA)',
    sla_note:
      'Dies ist die vertragliche Messung: alle 30 Sekunden eine Prüfung von /api/v1/ready auf jedem Host, über ' +
      'denselben öffentlichen Weg wie ein Besucher (Cloudflare und nginx). Releases und Wartung zählen normal mit; ' +
      'nichts wird ausgeschlossen. Die Norm von 99,5 % gilt pro genutztem Host und wird pro Host gemessen, nie als ' +
      'Durchschnitt. Siehe die',
    sla_terms: 'API-Bedingungen (Art. 10, auf Niederländisch)',
    col_month: 'Monat',
    col_availability: 'Verfügbarkeit',
    col_downtime: 'Ausfall',
    col_target: 'Ziel: {pct} %',
    downtime_none: 'keiner',
    sla_running: 'läuft noch',
    sla_met: 'erreicht',
    sla_missed: 'verfehlt',
    measured_from: '{month} (gemessen ab {day})',
    footer:
      'Die Prüfungen oben laufen alle paar Minuten über GitHub Actions, außerhalb des Netzwerks von PrijsProfeet, ' +
      'damit eine Störung dort diese Seite nicht mitreißt. Jeder Balken ist ein Tag (niederländische und belgische ' +
      'Zeit) über die letzten 31 Tage: grün heißt alles erfolgreich, orange ein Tag mit einem Vorfall, rot ein Tag, ' +
      'an dem jede Prüfung fehlschlug. Ein umrandeter Balken liegt vor dem Start dieses Hosts. Störungen und ' +
      'Mitteilungen verfolgen? Abonnieren Sie den',
    footer_feed: 'Atom-Feed (auf Niederländisch)',
  },
  en: {
    title: 'PrijsProfeet status',
    loading: 'Loading status…',
    choose_country: 'Choose your country',
    choose_lang: 'Choose your language',
    country_nl: 'Netherlands',
    country_be: 'Belgium',
    lang_nl: 'Nederlands',
    lang_fr: 'Français',
    lang_de: 'Deutsch',
    lang_en: 'English',
    updated: 'Updated {ago}',
    last_check: 'Last check: {when}',
    load_failed: 'Status data could not be loaded',
    banner: { up: 'All systems working', degraded: 'Partial outage', down: 'Outage', none: 'No data' },
    banner_stale: 'Data out of date: the last check was {ago}',
    status: { up: 'Operational', degraded: 'Degraded', down: 'Outage', none: 'No data' },
    no_recent_check: 'No recent check',
    checked: 'Checked {ago}',
    check_website: 'Website',
    check_mcp: 'In Claude and ChatGPT',
    check_api: 'API',
    reason: {
      ok: '200 OK',
      cloudflare: 'reachable (Cloudflare deliberately blocks automated checks here)',
      timeout: 'unreachable: timeout',
      reset: 'unreachable: connection reset',
      refused: 'unreachable: connection refused',
      incomplete: 'unreachable: incomplete response',
      dns: 'unreachable: DNS error',
      tls: 'unreachable: TLS error',
      unreachable: 'unreachable',
      mcp_invalid: 'no valid answer from the connector',
      mcp_tools: 'the connector is missing a tool (search or fetch)',
      not_fresh: 'last night’s data not updated by 07:00',
      http: 'HTTP {status}',
    },
    ago: {
      never: 'never',
      unknown: 'unknown',
      now: 'just now',
      minutes: (n) => (n === 1 ? '1 minute ago' : `${n} minutes ago`),
      hours: (n) => (n === 1 ? '1 hour ago' : `${n} hours ago`),
      days: (n) => `${n} days ago`,
    },
    duration: {
      under_minute: 'less than a minute',
      minutes: '{n} min',
      hours: '{h} h',
      hours_minutes: '{h} h {m} min',
      days: (n) => (n === 1 ? '1 day' : `${n} days`),
      days_hours: '{days} {h} h',
    },
    day_not_measured: 'Not measured yet',
    day_no_checks: 'No checks recorded',
    day_all_ok: '{runs} checks, all passed',
    day_failed: '{failed} of {runs} checks failed',
    day_no_incident: (n) => (n === 1 ? 'single check, no incident' : 'single checks, no incident'),
    incidents_title: 'Incidents',
    incidents_note:
      'Opened after two failed checks in a row (~10 min), so a single timeout is not an "incident". ' +
      'Covers what this probe sees itself, plus the outages our own monitoring reports publicly.',
    ongoing: 'Ongoing',
    since: 'since {start}',
    lasted: '{start}, lasted {duration}',
    not_fresh_incident: '{chain}: not fresh',
    notice_meta: 'Posted {published}',
    notice_until: ' · valid until {until}',
    freshness_title: 'Freshness per chain',
    freshness_note:
      'A chain is fresh when that night’s data is in by 07:00: the promotions refreshed and, where the chain ' +
      'supplies them, the shelf prices and barcodes at their usual level. Recorded every morning at 07:05 and not ' +
      'changed afterwards. The target is 90% fresh nights per chain per calendar month.',
    fresh_this_morning: 'This morning ({day})',
    fresh_on: 'On {day}',
    fresh_lead: '{when}: {fresh} of {total} chains fresh.',
    fresh_not: ' Not fresh: {names}.',
    fresh_unmeasured: ' Not measured yet: {names}.',
    fresh_details: 'Per chain and per month',
    col_chain: 'Chain',
    col_at_seven: 'At 07:00',
    running: ' (running)',
    not_measured: 'not measured yet',
    fresh: 'fresh',
    not_fresh: 'not fresh',
    business_title: 'For API customers',
    business_note:
      'The API and the Business SLA for this country. One key works on every host; the host decides the country of the data.',
    other_country: 'Using the API of another country? Pick that country at the top of the page.',
    sla_title: 'Monthly availability (Business SLA)',
    sla_note:
      'This is the contractual measurement: every 30 seconds a check of /api/v1/ready on each host, through the same ' +
      'public route as a visitor (Cloudflare and nginx). Releases and maintenance count as normal; nothing is ' +
      'excluded. The 99.5% target applies per host you use and is measured per host, never as an average. See the',
    sla_terms: 'API terms (art. 10, in Dutch)',
    col_month: 'Month',
    col_availability: 'Availability',
    col_downtime: 'Downtime',
    col_target: 'Target: {pct}%',
    downtime_none: 'none',
    sla_running: 'running',
    sla_met: 'met',
    sla_missed: 'missed',
    measured_from: '{month} (measured from {day})',
    footer:
      'The checks above run every few minutes from GitHub Actions, outside PrijsProfeet’s network, so an outage ' +
      'there does not take this page down with it. Each bar is one day (Dutch and Belgian time) over the last 31 ' +
      'days: green means everything passed, orange a day with an incident, red a day on which every check failed. ' +
      'An outlined bar falls before that host went live. Want to follow outages and notices? Subscribe to the',
    footer_feed: 'Atom feed',
  },
};

let lastData = null;
let lastFetchedAt = 0;
const choice = initialChoice();

/* ---------- language and country ---------- */

// URL first (a link from the site names the storefront), then what this
// visitor chose before, then the browser: nl-BE or fr-BE means België.
function initialChoice() {
  const params = new URLSearchParams(location.search);
  let stored = {};
  try {
    stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
  } catch {
    // Private mode or blocked storage: the page works without it.
  }
  const browser = navigator.languages || [navigator.language || ''];
  const country =
    [params.get('land'), stored.country, browser.some((l) => /-be$/i.test(l)) ? 'be' : null].find((c) =>
      COUNTRIES.includes(c),
    ) || 'nl';
  const langs = LANGS[country];
  const lang =
    [
      params.get('taal'),
      stored.lang,
      ...browser.map((l) => l.slice(0, 2).toLowerCase()).filter((l) => !NOT_FROM_BROWSER.includes(l)),
    ].find((l) => langs.includes(l)) || langs[0];
  return { country, lang };
}

function setChoice(country, lang) {
  choice.country = country;
  choice.lang = LANGS[country].includes(lang) ? lang : LANGS[country][0];
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(choice));
  } catch {
    // Not remembered, still shown.
  }
  history.replaceState(null, '', choiceUrl(choice.country, choice.lang));
  applyStaticText();
  renderSwitches();
  if (lastData) renderData(lastData);
}

function choiceUrl(country, lang) {
  const params = new URLSearchParams({ land: country });
  if (LANGS[country].length > 1) params.set('taal', lang);
  return `?${params}`;
}

// nl-NL, nl-BE, later fr-BE: number and date formats follow the storefront.
function locale() {
  return `${choice.lang}-${choice.country.toUpperCase()}`;
}

function lookup(table, path) {
  return path.split('.').reduce((node, part) => (node == null ? undefined : node[part]), table);
}

/* t('reason.timeout'), t('updated', {ago}), t('ago.minutes', 3) for a
 * plural. Falls back to Dutch, so a missing translation shows Dutch rather
 * than a key. */
function t(key, vars) {
  let value = lookup(STRINGS[choice.lang], key);
  if (value === undefined) value = lookup(STRINGS.nl, key);
  if (value === undefined) return key;
  if (typeof value === 'function') return value(vars);
  if (vars && typeof vars === 'object') {
    return value.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? vars[name] : m));
  }
  return value;
}

function applyStaticText() {
  document.documentElement.lang = choice.lang;
  document.querySelectorAll('[data-t]').forEach((node) => {
    node.textContent = t(node.dataset.t);
  });
  // The feed exists in Dutch and English (probe.py, FEED_TEXT); French readers
  // get the Dutch one, which their footer text says.
  document.getElementById('feed-link').href =
    choice.lang === 'en' ? 'data/incidents.en.xml' : 'data/incidents.xml';
}

function renderSwitches() {
  const countries = document.getElementById('country-switch');
  countries.setAttribute('aria-label', t('choose_country'));
  countries.replaceChildren(
    ...COUNTRIES.map((c) =>
      switchLink(t(`country_${c}`), choiceUrl(c, choice.lang), c === choice.country, () => setChoice(c, choice.lang)),
    ),
  );
  const langs = document.getElementById('lang-switch');
  const available = LANGS[choice.country];
  langs.hidden = available.length < 2;
  langs.setAttribute('aria-label', t('choose_lang'));
  langs.replaceChildren(
    ...available.map((l) =>
      switchLink(t(`lang_${l}`), choiceUrl(choice.country, l), l === choice.lang, () => setChoice(choice.country, l)),
    ),
  );
}

function switchLink(label, href, current, onPick) {
  const a = el('a', null, label);
  a.href = href;
  if (current) a.setAttribute('aria-current', 'true');
  a.addEventListener('click', (event) => {
    event.preventDefault();
    onPick();
  });
  return a;
}

/* ---------- formatting ---------- */

function monthLabel(month) {
  return new Intl.DateTimeFormat(locale(), { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(`${month}-01T00:00:00Z`),
  );
}

async function getJSON(path) {
  // Cache-busted: a reload is supposed to show the last few minutes, and a
  // CDN or browser holding a stale copy of this file is worse than no page.
  const res = await fetch(`${path}?t=${Date.now()}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function ago(iso) {
  if (!iso) return t('ago.never');
  const mins = minutesSince(iso);
  if (!Number.isFinite(mins)) return t('ago.unknown');
  if (mins < 1) return t('ago.now');
  if (mins < 60) return t('ago.minutes', mins);
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return t('ago.hours', hrs);
  return t('ago.days', Math.round(hrs / 24));
}

function minutesSince(iso) {
  return Math.round((Date.now() - new Date(iso).getTime()) / 60000);
}

function isStale(data) {
  if (!data.generated_at) return true;
  const mins = minutesSince(data.generated_at);
  return !Number.isFinite(mins) || mins > STALE_AFTER_MINUTES;
}

function formatDay(dateStr) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  return d.toLocaleDateString(locale(), { day: 'numeric', month: 'short', timeZone: 'UTC' });
}

// A stored reason code in the visitor's language. `http_503` reads as HTTP 503.
function reasonText(code) {
  if (!code) return '';
  if (code.startsWith('http_')) return t('reason.http', { status: code.slice(5) });
  const text = lookup(STRINGS[choice.lang], `reason.${code}`) ?? lookup(STRINGS.nl, `reason.${code}`);
  return text || code;
}

// The days (Dutch/Belgian time) an incident of this service touched. A day is only amber when
// an incident (INCIDENT_THRESHOLD consecutive failures in probe.py, ~10 min)
// fell on it: one timed-out check out of ~290 painted whole days amber while
// the incident list, by its own rule, stayed empty — two answers on one page.
function incidentDays(key, incidents) {
  const days = new Set();
  for (const incident of incidents || []) {
    if (incident.service !== key) continue;
    const start = new Date(incident.started_at);
    const end = incident.resolved_at ? new Date(incident.resolved_at) : new Date();
    for (let day = dayKey(start); day <= dayKey(end); day = addDays(day, 1)) days.add(day);
  }
  return days;
}

// probe.py keyed its buckets on UTC days until this date and on Dutch/Belgian
// days from it. The switch is exact: UTC day 2026-10-09 ended at 22:00 UTC,
// which is local midnight, so 10-09 holds 22 hours and no check counts twice.
// An older bar must look its incidents up in UTC too, or the 00:35 outage of
// 2026-10-07 turns the 07 bar amber while its failures sit in the 06 bucket.
const LOCAL_DAYS_FROM = '2026-10-10';

function dayKey(instant) {
  const utc = instant.toISOString().slice(0, 10);
  return utc < LOCAL_DAYS_FROM ? utc : localDay(instant);
}

// Days are keyed in Dutch/Belgian time (one timezone), matching probe.py's
// history buckets, the SLA months and the freshness mornings.
function localDay(instant) {
  // sv-SE formats as YYYY-MM-DD.
  return instant.toLocaleDateString('sv-SE', { timeZone: 'Europe/Amsterdam' });
}

// Calendar arithmetic on a YYYY-MM-DD key, done in UTC so no DST shift can
// skip or repeat a day.
function addDays(key, n) {
  const d = new Date(`${key}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function dayStatus(day, withIncident) {
  if (day.before) return 'before';
  if (!day.runs) return 'none';
  if (day.failed_runs === day.runs) return 'down';
  if (withIncident) return 'degraded';
  return 'up';
}

/* Index the stored history by date so the strip is built from the calendar,
 * not from the data. Days the collector never ran must show as gaps; drawing
 * only the days we have would silently compress an outage into a green run.
 * Days before a service's first check are a different thing — the host did
 * not exist yet (.be went live 2026-09-26) — and are marked as such rather
 * than shown as missing data. */
function buildStrip(history) {
  const byDate = new Map((history || []).map((d) => [d.date, d]));
  const first = (history || []).reduce((min, d) => (!min || d.date < min ? d.date : min), null);
  const days = [];
  const today = localDay(new Date());
  for (let i = WINDOW_DAYS - 1; i >= 0; i--) {
    const key = addDays(today, -i);
    days.push(byDate.get(key) || { date: key, runs: 0, failed_runs: 0, before: !first || key < first });
  }
  return days;
}

// The tip of a bar near either edge is anchored to that edge, or it runs off
// a phone screen.
const EDGE_BARS = 6;

function renderStrip(key, history, incidents) {
  const withIncident = incidentDays(key, incidents);
  const bars = el('div', 'bars');
  const days = buildStrip(history);
  days.forEach((day, index) => {
    const bar = el('div', 'bar');
    const incident = withIncident.has(day.date);
    bar.dataset.status = dayStatus(day, incident);
    if (index < EDGE_BARS) bar.dataset.edge = 'start';
    if (index >= days.length - EDGE_BARS) bar.dataset.edge = 'end';

    const lines = [formatDay(day.date)];
    if (day.before) {
      lines.push(t('day_not_measured'));
    } else if (!day.runs) {
      lines.push(t('day_no_checks'));
    } else if (!day.failed_runs) {
      lines.push(t('day_all_ok', { runs: day.runs }));
    } else {
      lines.push(t('day_failed', { failed: day.failed_runs, runs: day.runs }));
      if (!incident) lines.push(t('day_no_incident', day.failed_runs));
      const reason = day.first_failure && reasonText(day.first_failure.reason);
      if (reason) lines.push(reason);
    }
    // Focusable with a label, so the day is readable by keyboard, by screen
    // reader and by tapping on a phone (a tap focuses), not by hover alone.
    bar.tabIndex = 0;
    bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label', lines.join(', '));
    const tip = el('div', 'tip');
    tip.setAttribute('aria-hidden', 'true');
    lines.forEach((line, i) => {
      if (i > 0) tip.appendChild(document.createElement('br'));
      tip.appendChild(document.createTextNode(line));
    });
    bar.appendChild(tip);
    bars.appendChild(bar);
  });
  return bars;
}

function serviceStatus(service) {
  // A status from a check that is no longer recent says nothing about now.
  if (!service.last_checked || minutesSince(service.last_checked) > STALE_AFTER_MINUTES) {
    return 'none';
  }
  return service.status || 'none';
}

function renderService(key, service, incidents, label) {
  const row = el('div', 'service');

  const head = el('div', 'service-head');
  const name = el('span', 'service-name', label);
  if (service.target) name.title = service.target;
  head.appendChild(name);
  const statusKey = serviceStatus(service);
  const status = el(
    'span',
    'service-status',
    statusKey === 'none' && service.last_checked ? t('no_recent_check') : t(`status.${statusKey}`),
  );
  status.dataset.status = statusKey;
  head.appendChild(status);
  row.appendChild(head);

  row.appendChild(renderStrip(key, service.history, incidents));
  const reason = reasonText(service.reason);
  row.appendChild(
    el('div', 'service-meta', `${t('checked', { ago: ago(service.last_checked) })}${reason ? ` · ${reason}` : ''}`),
  );
  return row;
}

function servicesOf(services, country) {
  return Object.entries(services || {}).filter(([, s]) => s.country === country);
}

// An open alert incident (Prometheus, #954: slow API, failing requests, search
// down) means the checks above can all be green while something is wrong, so
// "Alles werkt" would contradict the list below it.
// The MCP connector down while the site and the API answer is a partial
// outage: most visitors are unaffected.
function overallStatus(entries, incidents) {
  const statuses = entries.map(([, s]) => (s.check === 'mcp' && s.status === 'down' ? 'degraded' : s.status));
  const alertOpen = (incidents || []).some((i) => i.resolved_at === null && i.kind === 'alert');
  if (statuses.every((s) => s === 'up') && !alertOpen) return 'up';
  if (statuses.some((s) => s === 'down')) return 'down';
  if (statuses.length === 0) return 'none';
  return 'degraded';
}

// The banner speaks for the chosen storefront: a .be outage does not turn a
// Dutch visitor's page red. The API counts, because the site runs on it.
function renderBanner(data) {
  const banner = document.getElementById('banner');
  const text = banner.querySelector('.banner-text');
  if (isStale(data)) {
    banner.dataset.status = 'none';
    text.textContent = data.generated_at ? t('banner_stale', { ago: ago(data.generated_at) }) : t('banner.none');
    return;
  }
  const status = overallStatus(servicesOf(data.services, choice.country), incidentsOf(data, choice.country));
  banner.dataset.status = status;
  text.textContent = t(`banner.${status}`);
}

function renderVisitorRows(data) {
  const main = document.getElementById('services');
  const panel = el('div', 'panel');
  const rows = servicesOf(data.services, choice.country)
    .filter(([, s]) => VISITOR_CHECKS.includes(s.check))
    .sort(([, a], [, b]) => VISITOR_CHECKS.indexOf(a.check) - VISITOR_CHECKS.indexOf(b.check));
  for (const [key, service] of rows) {
    panel.appendChild(renderService(key, service, data.incidents, t(`check_${service.check}`)));
  }
  main.replaceChildren(...(rows.length ? [panel] : []));
}

// The API row of the chosen storefront only, named by its host: an API
// customer thinks in hosts, and most use one. The country switch at the top
// reaches the others, so the block does not grow with every country.
function renderApiRows(data) {
  const panel = document.getElementById('api-rows');
  const rows = servicesOf(data.services, choice.country).filter(([, s]) => s.check === 'api');
  panel.replaceChildren(
    ...rows.map(([key, service]) =>
      renderService(key, service, data.incidents, `${t('check_api')} · ${HOSTS[service.country].replace(/^www\./, '')}`),
    ),
  );
  panel.hidden = !rows.length;
}

/* ---------- Business SLA ---------- */

// The Business SLA is owed per host and scored per host (#1242): one table
// for the chosen storefront's host, never an average.

// Verbatim digits, only the decimal separator follows the locale: rounding
// 99,9665 would make a figure the API states look more (or less) exact than it is.
function number(n) {
  return Number(n).toLocaleString(locale(), { maximumFractionDigits: 20 });
}

function renderSlaTable(summary) {
  // Months before the probe existed (`measured: false`) will never carry a
  // number — that isn't "not yet", it's permanent, so they're dropped rather
  // than shown as a wall of "nog niet gemeten" rows that never resolve.
  const months = (summary.months || []).filter((m) => m.measured);
  if (!months.length) return null;

  const table = el('table', 'sla-table');
  const head = table.insertRow();
  [t('col_month'), t('col_availability'), t('col_downtime'), t('col_target', { pct: number(summary.target_pct) })].forEach(
    (h) => {
      const th = document.createElement('th');
      th.textContent = h;
      head.appendChild(th);
    },
  );

  for (const m of months) {
    const row = table.insertRow();
    row.insertCell().textContent = slaMonthLabel(m);
    row.insertCell().textContent = m.availability_pct != null ? `${number(m.availability_pct)}%` : t('not_measured');
    // Minutes say more than a fourth decimal: "4 min" is what a partner weighs.
    row.insertCell().textContent =
      m.downtime_minutes == null
        ? '—'
        : m.downtime_minutes === 0
          ? t('downtime_none')
          : formatDuration(m.downtime_minutes * 60000);
    const verdict = row.insertCell();
    // A running month's `met` can still flip before it closes — reporting
    // "gehaald" on it would claim a verdict the month hasn't earned yet.
    if (!m.closed) {
      verdict.textContent = t('sla_running');
    } else if (m.met === true) {
      verdict.textContent = t('sla_met');
      verdict.className = 'met';
    } else if (m.met === false) {
      verdict.textContent = t('sla_missed');
      verdict.className = 'missed';
    } else {
      verdict.textContent = '—';
    }
  }
  return table;
}

// A month the probe did not cover from its first day (a host that went live
// mid-month, or the month the probe started) says so: 99,99% over five days
// is not the same claim as over a whole month. The running month is partial
// only because it has not ended, which "loopt nog" already says.
function slaMonthLabel(m) {
  const label = monthLabel(m.month);
  if (!m.partial || !m.measured_from) return label;
  const from = new Date(m.measured_from);
  if (localDay(from) === `${m.month}-01`) return label;
  const day = from.toLocaleDateString(locale(), { day: 'numeric', month: 'short', timeZone: 'Europe/Amsterdam' });
  return t('measured_from', { month: label, day });
}

function renderSla(slaByHost) {
  const section = document.getElementById('sla');
  const container = document.getElementById('sla-tables');
  container.innerHTML = '';
  const host = HOSTS[choice.country];
  const table = renderSlaTable((slaByHost || {})[host] || {});
  if (table) {
    container.appendChild(el('h4', 'sla-host', host.replace(/^www\./, '')));
    container.appendChild(scrollable(table));
  }
  section.hidden = !container.children.length;
}

// A table wider than a phone scrolls inside its own box, never the page.
function scrollable(table) {
  const wrap = el('div', 'table-wrap');
  wrap.appendChild(table);
  return wrap;
}

/* ---------- freshness ---------- */

// Freshness per chain (#1398): was each chain's data fresh at 07:00, and the
// share of fresh nights per calendar month against the norm. Only the chosen
// storefront: .be has its own chains. Only fresh or not, never the reason.
function todayAmsterdam() {
  return localDay(new Date());
}

function pct(ratio) {
  // Floored, so 89,96% never reads as a met 90%.
  return `${(Math.floor(ratio * 1000) / 10).toLocaleString(locale())}%`;
}

function sortedRetailers(summary) {
  return [...(summary.retailers || [])].sort((a, b) =>
    (a.name || a.retailer).localeCompare(b.name || b.retailer, locale()),
  );
}

// One line that answers the question a visitor comes with — was this
// morning's data in? — and names any chain that was not. The full table sits
// behind it: with every chain and month it is most of the page.
function freshnessSummary(summary) {
  const retailers = sortedRetailers(summary);
  const day = retailers.reduce((max, r) => (r.latest && (!max || r.latest.day > max) ? r.latest.day : max), null);
  if (!day) return null;
  const measured = retailers.filter((r) => r.latest && r.latest.day === day);
  const notFresh = measured.filter((r) => !r.latest.fresh).map((r) => r.name || r.retailer);
  const unmeasured = retailers.filter((r) => !r.latest || r.latest.day !== day).map((r) => r.name || r.retailer);

  const when =
    day === todayAmsterdam() ? t('fresh_this_morning', { day: formatDay(day) }) : t('fresh_on', { day: formatDay(day) });
  const p = el('p', 'fresh-summary');
  const fresh = measured.length - notFresh.length;
  const lead = el('span', null, t('fresh_lead', { when, fresh, total: measured.length }));
  lead.className = notFresh.length ? 'missed' : 'met';
  p.appendChild(lead);
  if (notFresh.length) p.appendChild(document.createTextNode(t('fresh_not', { names: notFresh.join(', ') })));
  if (unmeasured.length) {
    p.appendChild(document.createTextNode(t('fresh_unmeasured', { names: unmeasured.join(', ') })));
  }
  return p;
}

function renderFreshnessTable(summary) {
  const retailers = sortedRetailers(summary);
  if (!retailers.length) return null;
  const today = todayAmsterdam();
  const current = today.slice(0, 7);
  const months = [...new Set(retailers.flatMap((r) => (r.months || []).map((m) => m.month)))].sort().reverse();

  const table = el('table', 'sla-table');
  const head = table.insertRow();
  [t('col_chain'), t('col_at_seven'), ...months.map((m) => monthLabel(m) + (m === current ? t('running') : ''))].forEach(
    (h) => {
      const th = document.createElement('th');
      th.textContent = h;
      head.appendChild(th);
    },
  );

  for (const r of retailers) {
    const row = table.insertRow();
    row.insertCell().textContent = r.name || r.retailer;

    const latest = row.insertCell();
    if (!r.latest) {
      latest.textContent = t('not_measured');
    } else {
      latest.textContent = r.latest.fresh ? t('fresh') : t('not_fresh');
      // A morning that was not frozen yet must not read as today's answer.
      if (r.latest.day !== today) latest.textContent += ` (${formatDay(r.latest.day)})`;
      latest.className = r.latest.fresh ? 'met' : 'missed';
    }

    for (const month of months) {
      const cell = row.insertCell();
      const m = (r.months || []).find((x) => x.month === month);
      if (!m) {
        cell.textContent = '—';
        continue;
      }
      cell.textContent = `${pct(m.ratio)} (${m.fresh_nights}/${m.nights})`;
      // Same rule as the SLA: a running month has no verdict yet.
      if (month !== current) cell.className = m.meets_norm ? 'met' : 'missed';
    }
  }
  return table;
}

function renderFreshness(byHostData) {
  const section = document.getElementById('freshness');
  const container = document.getElementById('freshness-tables');
  container.innerHTML = '';
  const summary = (byHostData || {})[HOSTS[choice.country]];
  const table = summary && renderFreshnessTable(summary);
  if (table) {
    const line = freshnessSummary(summary);
    if (line) container.appendChild(line);
    const details = el('details', 'fresh-details');
    details.appendChild(el('summary', null, t('fresh_details')));
    details.appendChild(scrollable(table));
    container.appendChild(details);
  }
  section.hidden = !container.children.length;
}

function formatDuration(ms) {
  const mins = Math.round(ms / 60000);
  if (mins < 1) return t('duration.under_minute');
  if (mins < 60) return t('duration.minutes', { n: mins });
  const hrs = Math.floor(mins / 60);
  const rest = mins % 60;
  if (hrs >= 24) {
    const days = t('duration.days', Math.floor(hrs / 24));
    const h = hrs % 24;
    return h ? t('duration.days_hours', { days, h }) : days;
  }
  return rest ? t('duration.hours_minutes', { h: hrs, m: rest }) : t('duration.hours', { h: hrs });
}

function formatDateTime(iso) {
  return new Date(iso).toLocaleString(locale(), {
    timeZone: 'Europe/Amsterdam',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/* ---------- notices and incidents ---------- */

// Hand-written notices from notices.json (validated by probe.py). Only the
// ones in force now are shown; the feed keeps them all.
function renderNotices(notices) {
  const section = document.getElementById('notices');
  const now = Date.now();
  const current = (notices || []).filter((n) => new Date(n.published) <= now && (!n.until || new Date(n.until) > now));
  section.hidden = !current.length;
  section.innerHTML = '';
  for (const n of current) {
    const item = el('div', 'notice');
    const english = choice.lang === 'en' && n.title_en;
    item.appendChild(el('div', 'notice-title', english ? n.title_en : n.title));
    item.appendChild(el('div', 'notice-body', english ? n.body_en : n.body));
    item.appendChild(
      el(
        'div',
        'incident-meta',
        t('notice_meta', { published: formatDateTime(n.published) }) +
          (n.until ? t('notice_until', { until: formatDateTime(n.until) }) : ''),
      ),
    );
    section.appendChild(item);
  }
}

function incidentsOf(data, country) {
  return (data.incidents || []).filter((i) => i.country === country);
}

// What the page calls an incident, in the visitor's language. The stored
// `name` is the feed's Dutch and is not read here. An alert carries the app's
// own titles: French when the app sent one (prijsprofeet#1568), else Dutch.
function incidentTitle(incident) {
  if (incident.kind === 'versheid') return t('not_fresh_incident', { chain: incident.chain });
  if (incident.kind === 'alert') {
    const fallback = TITLE_FALLBACK[choice.lang];
    return incident[`title_${choice.lang}`] || (fallback && incident[`title_${fallback}`]) || incident.title;
  }
  return t(`check_${incident.service.replace(/_be$/, '')}`);
}

function renderIncidents(data) {
  const section = document.getElementById('incidents');
  const list = incidentsOf(data, choice.country).slice(0, 20);
  section.hidden = !list.length;
  const ul = document.getElementById('incidents-list');
  ul.innerHTML = '';
  for (const incident of list) {
    const li = el('li', 'incident');
    const ongoing = incident.resolved_at === null;
    li.dataset.ongoing = String(ongoing);

    const head = el('div', 'incident-head');
    head.appendChild(document.createTextNode(incidentTitle(incident)));
    if (ongoing) head.appendChild(el('span', null, t('ongoing')));
    li.appendChild(head);

    const start = formatDateTime(incident.started_at);
    const when = ongoing
      ? t('since', { start })
      : t('lasted', {
          start,
          duration: formatDuration(new Date(incident.resolved_at) - new Date(incident.started_at)),
        });
    // A stale chain's title already says what is wrong.
    const reason = incident.kind === 'versheid' ? '' : reasonText(incident.reason);
    li.appendChild(el('div', 'incident-meta', `${when}${reason ? ` — ${reason}` : ''}`));

    ul.appendChild(li);
  }
}

/* ---------- page ---------- */

function renderData(data) {
  document.getElementById('checked').textContent = t('updated', { ago: ago(data.generated_at) });
  document.getElementById('build').textContent = data.generated_at
    ? t('last_check', { when: formatDateTime(data.generated_at) })
    : '';

  renderBanner(data);
  renderNotices(data.notices);
  renderVisitorRows(data);
  renderFreshness(data.freshness);
  renderIncidents(data);
  renderApiRows(data);
  renderSla(data.sla);
}

/* Re-renders the "Bijgewerkt N geleden" line between polls, without a network
 * call — so the page doesn't need a fresh fetch just to stop lying about
 * how old the data on screen is. */
function tickClock() {
  if (!lastData) return;
  document.getElementById('checked').textContent = t('updated', { ago: ago(lastData.generated_at) });
  // The banner turns stale between polls too, without a new fetch.
  renderBanner(lastData);
}

async function refresh() {
  try {
    const data = await getJSON('data/status.json');
    lastData = data;
    lastFetchedAt = Date.now();
    renderData(data);
  } catch (err) {
    // Leave whatever last rendered successfully on screen — replacing a
    // real (if slightly stale) status with "could not load" on a single
    // failed poll would be a worse answer than the one already showing.
    if (!lastData) {
      document.getElementById('checked').textContent = t('load_failed');
    }
  }
}

function startPolling() {
  applyStaticText();
  renderSwitches();
  refresh();
  setInterval(tickClock, CLOCK_TICK_MS);
  setInterval(() => {
    if (document.visibilityState === 'visible') refresh();
  }, POLL_INTERVAL_MS);

  // A tab left in the background for a while is showing data far older than
  // POLL_INTERVAL_MS by the time it's looked at again — catch up the moment
  // it becomes visible instead of waiting for the next tick.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - lastFetchedAt > POLL_INTERVAL_MS) {
      refresh();
    }
  });
}

startPolling();
