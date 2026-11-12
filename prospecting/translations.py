"""The first photographer email, its follow-up and the signature in the 13 languages of
the leads (written 2026-11-12; edit them in the app).

`fill_known` adds them to any template whose French text is exactly one of these, and to
the signature, once: a translation you later remove or change stays as you left it.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

# (subject, body) by language. The French one is the user's own text.
FIRST_EMAIL: dict[str, tuple[str, str]] = {
    "fr": ("Retouche automatique de vos photos immobilières ?",
           "Bonjour {prenom},\n\n"
           "Je développe Veralume, un outil qui automatise la retouche de photos immobilières "
           "(lumière, couleurs, perspectives, suppression d'objets), sans intervention manuelle.\n\n"
           "Je vous joins quelques exemples avant/après, réalisés entièrement automatiquement.\n\n"
           "Je cherche quelques photographes pour le tester sur de vraies photos et avoir leur "
           "avis de professionnel.\n\n"
           "Ça vous dirait que je retouche gratuitement quelques-unes de vos photos pour voir ce "
           "que ça donne ?\n\n"
           "Bonne journée,\n\n{signature}"),
    "en": ("Automatic editing for your real estate photos?",
           "Hi {prenom},\n\n"
           "I'm building Veralume, a tool that automates real estate photo editing (lighting, "
           "colours, perspective, object removal) with no manual work.\n\n"
           "I've attached a few before/after examples, all done fully automatically.\n\n"
           "I'm looking for a few photographers to try it on real photos and give me their "
           "professional opinion.\n\n"
           "Would you like me to edit a few of your photos for free, to see what it does?\n\n"
           "Have a good day,\n\n{signature}"),
    "de": ("Automatische Bearbeitung Ihrer Immobilienfotos?",
           "Hallo {prenom},\n\n"
           "ich entwickle Veralume, ein Tool, das die Bearbeitung von Immobilienfotos "
           "automatisiert (Licht, Farben, Perspektive, Entfernen von Gegenständen), ganz ohne "
           "manuelle Arbeit.\n\n"
           "Im Anhang finden Sie einige Vorher-Nachher-Beispiele, vollständig automatisch "
           "erstellt.\n\n"
           "Ich suche einige Fotografinnen und Fotografen, die es mit echten Fotos testen und "
           "mir ihre fachliche Meinung geben.\n\n"
           "Hätten Sie Lust, dass ich einige Ihrer Fotos kostenlos bearbeite, damit Sie sehen, "
           "was dabei herauskommt?\n\n"
           "Viele Grüße\n\n{signature}"),
    "nl": ("Automatische nabewerking van uw vastgoedfoto's?",
           "Hallo {prenom},\n\n"
           "Ik ontwikkel Veralume, een tool die de nabewerking van vastgoedfoto's automatiseert "
           "(licht, kleuren, perspectief, objecten verwijderen), zonder handmatig werk.\n\n"
           "In de bijlage vindt u enkele voor-en-na-voorbeelden, volledig automatisch gemaakt.\n\n"
           "Ik zoek een paar fotografen die het op echte foto's willen testen en mij hun "
           "professionele mening willen geven.\n\n"
           "Zou u het leuk vinden als ik gratis een paar van uw foto's bewerk, om te zien wat "
           "het oplevert?\n\n"
           "Met vriendelijke groet,\n\n{signature}"),
    "it": ("Fotoritocco automatico delle sue foto immobiliari?",
           "Buongiorno {prenom},\n\n"
           "sto sviluppando Veralume, uno strumento che automatizza il fotoritocco delle foto "
           "immobiliari (luce, colori, prospettiva, rimozione di oggetti), senza alcun "
           "intervento manuale.\n\n"
           "Le allego alcuni esempi prima/dopo, realizzati in modo completamente automatico.\n\n"
           "Sto cercando alcuni fotografi che lo provino su foto reali e mi diano il loro parere "
           "professionale.\n\n"
           "Le piacerebbe che ritoccassi gratuitamente alcune delle sue foto, per vedere il "
           "risultato?\n\n"
           "Buona giornata,\n\n{signature}"),
    "es": ("¿Retoque automático de sus fotos inmobiliarias?",
           "Hola, {prenom}:\n\n"
           "Estoy desarrollando Veralume, una herramienta que automatiza el retoque de fotos "
           "inmobiliarias (luz, colores, perspectiva, eliminación de objetos), sin intervención "
           "manual.\n\n"
           "Le adjunto algunos ejemplos de antes y después, hechos de forma totalmente "
           "automática.\n\n"
           "Busco algunos fotógrafos que lo prueben con fotos reales y me den su opinión "
           "profesional.\n\n"
           "¿Le gustaría que retocara gratis algunas de sus fotos para ver el resultado?\n\n"
           "Un saludo,\n\n{signature}"),
    "pt": ("Edição automática das suas fotos imobiliárias?",
           "Olá {prenom},\n\n"
           "Estou a desenvolver o Veralume, uma ferramenta que automatiza a edição de fotos "
           "imobiliárias (luz, cores, perspetiva, remoção de objetos), sem trabalho manual.\n\n"
           "Envio em anexo alguns exemplos de antes e depois, feitos de forma totalmente "
           "automática.\n\n"
           "Procuro alguns fotógrafos que o queiram testar com fotos reais e me deem a sua "
           "opinião profissional.\n\n"
           "Gostaria que eu editasse gratuitamente algumas das suas fotos, para ver o "
           "resultado?\n\n"
           "Com os melhores cumprimentos,\n\n{signature}"),
    "sv": ("Automatisk redigering av dina fastighetsbilder?",
           "Hej {prenom},\n\n"
           "Jag utvecklar Veralume, ett verktyg som automatiserar redigeringen av "
           "fastighetsbilder (ljus, färger, perspektiv, borttagning av föremål) helt utan "
           "manuellt arbete.\n\n"
           "Jag bifogar några före/efter-exempel, gjorda helt automatiskt.\n\n"
           "Jag söker några fotografer som vill testa det på riktiga bilder och ge mig sin "
           "professionella åsikt.\n\n"
           "Vill du att jag redigerar några av dina bilder gratis, så att du kan se "
           "resultatet?\n\n"
           "Ha en fin dag,\n\n{signature}"),
    "da": ("Automatisk redigering af dine ejendomsbilleder?",
           "Hej {prenom}\n\n"
           "Jeg udvikler Veralume, et værktøj der automatiserer redigering af ejendomsbilleder "
           "(lys, farver, perspektiv, fjernelse af genstande) helt uden manuelt arbejde.\n\n"
           "Jeg har vedhæftet et par før/efter-eksempler, lavet helt automatisk.\n\n"
           "Jeg leder efter nogle fotografer, der vil teste det på rigtige billeder og give mig "
           "deres faglige vurdering.\n\n"
           "Har du lyst til, at jeg redigerer et par af dine billeder gratis, så du kan se "
           "resultatet?\n\n"
           "Hav en god dag\n\n{signature}"),
    "no": ("Automatisk redigering av eiendomsbildene dine?",
           "Hei {prenom},\n\n"
           "Jeg utvikler Veralume, et verktøy som automatiserer redigering av eiendomsbilder "
           "(lys, farger, perspektiv, fjerning av gjenstander) helt uten manuelt arbeid.\n\n"
           "Vedlagt finner du noen før/etter-eksempler, laget helt automatisk.\n\n"
           "Jeg ser etter noen fotografer som vil teste det på ekte bilder og gi meg sin faglige "
           "vurdering.\n\n"
           "Har du lyst til at jeg redigerer noen av bildene dine gratis, så du kan se "
           "resultatet?\n\n"
           "Ha en fin dag,\n\n{signature}"),
    "fi": ("Kiinteistökuviesi automaattinen käsittely?",
           "Hei {prenom},\n\n"
           "Kehitän Veralumea, työkalua, joka automatisoi kiinteistökuvien jälkikäsittelyn "
           "(valo, värit, perspektiivi, esineiden poisto) ilman käsityötä.\n\n"
           "Liitteenä on muutama ennen/jälkeen-esimerkki, jotka on tehty täysin "
           "automaattisesti.\n\n"
           "Etsin muutamaa valokuvaajaa, jotka kokeilisivat sitä oikeilla kuvilla ja kertoisivat "
           "ammattilaisen mielipiteensä.\n\n"
           "Haluaisitko, että käsittelen muutaman kuvistasi maksutta, jotta näet tuloksen?\n\n"
           "Mukavaa päivänjatkoa,\n\n{signature}"),
    "pl": ("Automatyczna obróbka Państwa zdjęć nieruchomości?",
           "Dzień dobry {prenom},\n\n"
           "tworzę Veralume, narzędzie, które automatyzuje obróbkę zdjęć nieruchomości "
           "(światło, kolory, perspektywa, usuwanie przedmiotów) bez ręcznej pracy.\n\n"
           "W załączniku przesyłam kilka przykładów przed i po, wykonanych w pełni "
           "automatycznie.\n\n"
           "Szukam kilku fotografów, którzy przetestowaliby je na prawdziwych zdjęciach i "
           "podzielili się swoją profesjonalną opinią.\n\n"
           "Czy chcieliby Państwo, abym bezpłatnie obrobił kilka Państwa zdjęć, żeby zobaczyć "
           "efekt?\n\n"
           "Pozdrawiam serdecznie\n\n{signature}"),
    "cs": ("Automatické úpravy vašich fotografií nemovitostí?",
           "Dobrý den {prenom},\n\n"
           "vyvíjím Veralume, nástroj, který automatizuje úpravy fotografií nemovitostí (světlo, "
           "barvy, perspektiva, odstranění předmětů) bez ruční práce.\n\n"
           "V příloze posílám několik ukázek před a po, vytvořených zcela automaticky.\n\n"
           "Hledám několik fotografů, kteří by ho vyzkoušeli na skutečných fotografiích a dali "
           "mi svůj profesionální názor.\n\n"
           "Máte zájem, abych zdarma upravil několik vašich fotografií, ať vidíte výsledek?\n\n"
           "Hezký den\n\n{signature}"),
}

# The follow-up body by language (its subject is always "Re: " + the first email's).
FOLLOWUP: dict[str, str] = {
    "fr": "Bonjour {prenom},\n\n"
          "Je me permets de revenir vers vous, au cas où mon premier message serait passé inaperçu.\n\n"
          "Si vous êtes curieux de voir ce que Veralume donne sur vos propres photos, envoyez-m'en "
          "simplement 2 ou 3 en réponse : je vous les renvoie retouchées, gratuitement et sans "
          "engagement.\n\n"
          "Et si ce n'est pas le bon moment, un simple « non merci » me suffit.\n\n"
          "Bonne journée,\n\n{signature}",
    "en": "Hi {prenom},\n\n"
          "I'm following up in case my first message slipped through the cracks.\n\n"
          "If you're curious to see what Veralume does with your own photos, just reply with 2 or "
          "3 of them: I'll send them back edited, free and with no strings attached.\n\n"
          "And if now isn't the right time, a simple \"no thanks\" is perfectly fine.\n\n"
          "Have a good day,\n\n{signature}",
    "de": "Hallo {prenom},\n\n"
          "ich melde mich noch einmal, falls meine erste Nachricht untergegangen ist.\n\n"
          "Wenn Sie neugierig sind, was Veralume aus Ihren eigenen Fotos macht, schicken Sie mir "
          "einfach 2 oder 3 davon als Antwort: Ich sende sie Ihnen bearbeitet zurück, kostenlos "
          "und unverbindlich.\n\n"
          "Und falls es gerade nicht passt, genügt mir ein kurzes „Nein, danke“.\n\n"
          "Viele Grüße\n\n{signature}",
    "nl": "Hallo {prenom},\n\n"
          "Ik kom nog even terug op mijn vorige bericht, voor het geval het tussen de andere "
          "mails is beland.\n\n"
          "Bent u benieuwd wat Veralume met uw eigen foto's doet? Stuur er dan gewoon 2 of 3 als "
          "antwoord: ik stuur ze bewerkt terug, gratis en vrijblijvend.\n\n"
          "En als het nu niet uitkomt, is een kort „nee, bedankt” ook prima.\n\n"
          "Met vriendelijke groet,\n\n{signature}",
    "it": "Buongiorno {prenom},\n\n"
          "mi permetto di scriverle di nuovo, nel caso il mio primo messaggio le fosse sfuggito.\n\n"
          "Se le interessa vedere cosa fa Veralume con le sue foto, me ne invii semplicemente 2 o "
          "3 in risposta: gliele rimando ritoccate, gratis e senza impegno.\n\n"
          "E se non è il momento giusto, mi basta un semplice «no, grazie».\n\n"
          "Buona giornata,\n\n{signature}",
    "es": "Hola, {prenom}:\n\n"
          "Le escribo de nuevo por si mi primer mensaje se le pasó.\n\n"
          "Si le interesa ver qué hace Veralume con sus propias fotos, envíeme 2 o 3 en "
          "respuesta: se las devuelvo retocadas, gratis y sin compromiso.\n\n"
          "Y si no es buen momento, con un simple «no, gracias» me basta.\n\n"
          "Un saludo,\n\n{signature}",
    "pt": "Olá {prenom},\n\n"
          "Volto a escrever, caso a minha primeira mensagem tenha passado despercebida.\n\n"
          "Se tiver curiosidade em ver o que o Veralume faz com as suas próprias fotos, basta "
          "enviar-me 2 ou 3 em resposta: devolvo-as editadas, de forma gratuita e sem "
          "compromisso.\n\n"
          "E se não for o momento certo, basta-me um simples «agora não».\n\n"
          "Com os melhores cumprimentos,\n\n{signature}",
    "sv": "Hej {prenom},\n\n"
          "Jag hör av mig igen, ifall mitt första mejl försvann bland alla andra.\n\n"
          "Om du är nyfiken på vad Veralume gör med dina egna bilder, svara bara med 2 eller 3 av "
          "dem: jag skickar tillbaka dem redigerade, gratis och utan förpliktelser.\n\n"
          "Och om det inte passar just nu räcker det med ett enkelt ”nej tack”.\n\n"
          "Ha en fin dag,\n\n{signature}",
    "da": "Hej {prenom}\n\n"
          "Jeg skriver igen, hvis nu min første mail er druknet i indbakken.\n\n"
          "Hvis du er nysgerrig efter at se, hvad Veralume gør med dine egne billeder, så svar "
          "bare med 2 eller 3 af dem: Jeg sender dem redigeret tilbage, gratis og helt "
          "uforpligtende.\n\n"
          "Og passer det ikke lige nu, er et kort ”nej tak” helt fint.\n\n"
          "Hav en god dag\n\n{signature}",
    "no": "Hei {prenom},\n\n"
          "Jeg tar kontakt igjen, i tilfelle den første e-posten min druknet i innboksen.\n\n"
          "Hvis du er nysgjerrig på hva Veralume gjør med dine egne bilder, er det bare å svare "
          "med 2 eller 3 av dem: Jeg sender dem tilbake redigert, gratis og helt "
          "uforpliktende.\n\n"
          "Og passer det ikke akkurat nå, holder det med et kort «nei takk».\n\n"
          "Ha en fin dag,\n\n{signature}",
    "fi": "Hei {prenom},\n\n"
          "Palaan vielä asiaan siltä varalta, että ensimmäinen viestini hukkui muiden "
          "joukkoon.\n\n"
          "Jos olet utelias näkemään, mitä Veralume tekee omille kuvillesi, vastaa vain "
          "lähettämällä 2 tai 3 kuvaa: lähetän ne takaisin käsiteltyinä, maksutta ja ilman "
          "sitoumuksia.\n\n"
          "Ja jos aika ei ole nyt sopiva, pelkkä ”ei kiitos” riittää.\n\n"
          "Mukavaa päivänjatkoa,\n\n{signature}",
    "pl": "Dzień dobry {prenom},\n\n"
          "odzywam się ponownie, na wypadek gdyby moja pierwsza wiadomość umknęła Państwa "
          "uwadze.\n\n"
          "Jeśli są Państwo ciekawi, jak Veralume poradzi sobie z Państwa zdjęciami, wystarczy "
          "odpowiedzieć, załączając 2 lub 3 z nich: odeślę je obrobione, bezpłatnie i bez "
          "zobowiązań.\n\n"
          "A jeśli to nie jest dobry moment, wystarczy krótkie „nie, dziękuję”.\n\n"
          "Pozdrawiam serdecznie\n\n{signature}",
    "cs": "Dobrý den {prenom},\n\n"
          "ozývám se znovu pro případ, že vám moje první zpráva zapadla.\n\n"
          "Pokud vás zajímá, co Veralume udělá s vašimi vlastními fotografiemi, stačí mi "
          "v odpovědi poslat 2 nebo 3 z nich: pošlu vám je zpět upravené, zdarma a "
          "nezávazně.\n\n"
          "A pokud teď není vhodná chvíle, stačí krátké „ne, děkuji“.\n\n"
          "Hezký den\n\n{signature}",
}

SIGNATURE: dict[str, str] = {
    "fr": "Yacine\nCréateur de Veralume",
    "en": "Yacine\nFounder of Veralume",
    "de": "Yacine\nGründer von Veralume",
    "nl": "Yacine\nOprichter van Veralume",
    "it": "Yacine\nFondatore di Veralume",
    "es": "Yacine\nFundador de Veralume",
    "pt": "Yacine\nFundador da Veralume",
    "sv": "Yacine\nGrundare av Veralume",
    "da": "Yacine\nStifter af Veralume",
    "no": "Yacine\nGründer av Veralume",
    "fi": "Yacine\nVeralumen perustaja",
    "pl": "Yacine\nTwórca Veralume",
    "cs": "Yacine\nZakladatel Veralume",
}

FOLLOWUP_SUBJECT = "Re:"  # marks a follow-up's language as written (see campaigns.set_followup)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'")).strip()


def followup_translations() -> dict[str, dict[str, str]]:
    """The follow-up in every language but French, as a template's translations."""
    return {k: {"subject": FOLLOWUP_SUBJECT, "body": v} for k, v in FOLLOWUP.items() if k != "fr"}


def first_email_translations() -> dict[str, dict[str, str]]:
    return {k: {"subject": s, "body": b} for k, (s, b) in FIRST_EMAIL.items() if k != "fr"}


def known_translations(subject: str, body: str, step: int) -> dict[str, dict[str, str]] | None:
    """The translations of this French text if it's one of ours, else None."""
    if step == 0 and _norm(subject) == _norm(FIRST_EMAIL["fr"][0]) \
            and _norm(body) == _norm(FIRST_EMAIL["fr"][1]):
        return first_email_translations()
    if step == 1 and _norm(body) == _norm(FOLLOWUP["fr"]):
        return followup_translations()
    return None


def fill_known(s: Session) -> dict[str, int]:
    """Add the translations to our two French texts wherever they are (general templates,
    campaign versions, campaign follow-ups) and to the signature. Each template and the
    signature are done once (remembered in settings), never over a language already written.
    The caller commits."""
    from prospecting import mailer
    from prospecting.db import Template

    done = set(mailer.get_setting(s, "translated_templates", []))
    out = {"templates": 0, "signatures": 0}
    for t in s.scalars(select(Template).where(Template.archived.is_(False))):
        if t.id in done or (t.language or "fr") != "fr":
            continue
        tr = known_translations(t.subject, t.body, t.step)
        if tr is None:
            continue
        have = dict(t.translations or {})
        t.translations = {**tr, **{k: v for k, v in have.items() if (v.get("body") or "").strip()}}
        done.add(t.id)
        out["templates"] += 1
    if out["templates"]:
        mailer.set_setting(s, "translated_templates", sorted(done))
    if not mailer.get_setting(s, "translated_signatures", False):
        fr = mailer.get_setting(s, "signature", mailer.DEFAULT_SIGNATURE)
        if _norm(fr) == _norm(SIGNATURE["fr"]):
            sigs: dict[str, Any] = dict(mailer.get_setting(s, "signatures", {}) or {})
            for k, v in SIGNATURE.items():
                if k != "fr" and not (sigs.get(k) or "").strip():
                    sigs[k] = v
                    out["signatures"] += 1
            mailer.set_setting(s, "signatures", sigs)
            mailer.set_setting(s, "translated_signatures", True)
    return out
