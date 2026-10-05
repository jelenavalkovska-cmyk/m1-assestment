# CR-C · Atbildes termiņa pagarināšana: piemēri

Piemēri pieteikumam [tracker/CR-C.md](../tracker/CR-C.md) un galapunktam
`POST /submissions/{id}/extend` ([openapi.yaml](openapi.yaml)). Visi dati ir sintētiski.

Noteikumi:
- Pagarināt var tikai statusos `RECEIVED` un `IN_PROGRESS`, citādi 409 `INVALID_STATE`.
- Jaunajam termiņam jābūt vēlākam par pašreizējo termiņu un par šodienu (UTC).
- Jaunais termiņš nedrīkst būt vēlāks par saņemšanas datumu + 4 mēneši (robeža iekļauta).
  Ja mērķa mēnesī nav saņemšanas dienas datuma, pārpalikušās dienas pārnes uz nākamo mēnesi.
- Ja kāds noteikums nav izpildīts: 400 `INVALID_DUE_DATE`, termiņš nemainās.

Šodiena: 2026-10-05 (UTC). Rindai ar saņemšanu 2027-10-31 šodiena ir 2027-11-01.
Vērtības iegūtas, izsaucot API.

| Statuss | Saņemts | Pašreizējais termiņš | Vēlākais iespējamais termiņš | Pieprasītais jaunais termiņš | Rezultāts | Termiņš pēc pagarināšanas |
|---|---|---|---|---|---|---|
| `RECEIVED` | 2026-09-25 | 2026-10-26 | 2027-01-25 | 2026-12-15 | 200 | **2026-12-15** |
| `RECEIVED` | 2026-09-25 | 2026-10-26 | 2027-01-25 | 2027-01-25 | 200, robeža iekļauta | **2027-01-25** |
| `RECEIVED` | 2026-09-25 | 2026-10-26 | 2027-01-25 | 2027-01-26 | 400 `INVALID_DUE_DATE`, vēlāks par 4 mēnešiem | 2026-10-26 (nemainās) |
| `IN_PROGRESS` | 2026-09-25 | 2026-10-26 | 2027-01-25 | 2026-10-26 | 400 `INVALID_DUE_DATE`, vienāds ar pašreizējo | 2026-10-26 (nemainās) |
| `IN_PROGRESS` | 2026-09-25 | 2026-10-26 | 2027-01-25 | 2026-10-20 | 400 `INVALID_DUE_DATE`, agrāks par pašreizējo | 2026-10-26 (nemainās) |
| `IN_PROGRESS` | 2026-07-01 | 2026-07-31 | 2026-11-01 | 2026-10-05 | 400 `INVALID_DUE_DATE`, šodien | 2026-07-31 (nemainās) |
| `IN_PROGRESS` | 2026-07-01 | 2026-07-31 | 2026-11-01 | 2026-10-06 | 200, rītdiena | **2026-10-06** |
| `RECEIVED` | 2026-10-31 | 2026-11-30 | 2027-03-03 (dienu pārnešana) | 2027-03-03 | 200 | **2027-03-03** |
| `RECEIVED` | 2026-10-31 | 2026-11-30 | 2027-03-03 (dienu pārnešana) | 2027-03-04 | 400 `INVALID_DUE_DATE` | 2026-11-30 (nemainās) |
| `IN_PROGRESS` | 2026-05-31 | 2026-06-30 | 2026-10-01 (dienu pārnešana) | 2026-10-01 | 400 `INVALID_DUE_DATE`, robeža jau pagātnē | 2026-06-30 (nemainās) |
| `RECEIVED` | 2027-10-31 | 2027-11-30 | 2028-03-02 (garais gads) | 2028-03-02 | 200 | **2028-03-02** |
| `FORWARDED` | 2026-09-14 | 2026-10-14 | — | 2026-12-01 | 409 `INVALID_STATE` | 2026-10-14 (nemainās) |
| `ANSWERED` | 2026-09-01 | 2026-10-01 | — | 2026-12-01 | 409 `INVALID_STATE` | 2026-10-01 (nemainās) |
| `WITHDRAWN` | 2026-09-10 | 2026-10-12 | — | 2026-12-01 | 409 `INVALID_STATE` | 2026-10-12 (nemainās) |

Piezīme: ja 4 mēnešu robeža jau ir pagātnē (piemēram, saņemts 2026-05-31), iesniegumu
vairs nevar pagarināt. Pieteikumā tas nav aprakstīts, to jāapstiprina produkta īpašniekam.
