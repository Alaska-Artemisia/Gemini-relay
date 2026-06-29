#!/usr/bin/env python3
# Me + Lia - geo fix: set location.country = United States for 124 unresolved profiles.
# Surgical: updates ONLY location. No list membership, no consent change, no flow re-trigger.
# Runs on your Mac using your KLAVIYO_KEY env var. Nothing is sent to chat.
import os, json, urllib.request, urllib.error
KEY=os.environ.get("KLAVIYO_KEY")
if not KEY:
    raise SystemExit("KLAVIYO_KEY not set in this shell. Open the terminal where mk works.")
REV="2025-01-15"
EMAILS=["pag2g1b@hotmail.com", "jamieyemmans@gmail.com", "catherine@catherinechuang.com", "nadvis0717@icloud.com", "catwatcher315@yahoo.com", "jessicaibm4@gmail.com", "cherylcummings74@yahoo.com", "hamberlinclan@gmail.com", "mlsabby@aol.com", "cindycoren921@gmail.com", "garcialuisalonso000@gmail.com", "krystalc49@gmail.com", "maria.carterallen@gmail.com", "jessi_hopper@hotmail.com", "samantha.ulven@yahoo.com", "tsimp2015@gmail.com", "sedasasha@yahoo.com", "dglnn.ski@gmail.com", "annabanana59@gmail.com", "ablovesnj@gmail.com", "kelseymjames.17@gmail.com", "l.yevmen@outlook.com", "keely.geringer@gmail.com", "twstdsis511@att.net", "antoff.leanna@gmail.com", "moxie_@hotmail.com", "yojiannev71@yahoo.com", "hennhousekids@gmail.com", "dealsaubrianna@gmail.com", "shelby.leslie1996@gmail.com", "amandamandato613@gmail.com", "alinadbanu@gmail.com", "jsbonthron@gmail.com", "nicmoyer5@yahoo.com", "inessawilliams@gmail.com", "bobbikingen@gmail.com", "rachelhardt14@gmail.com", "linnea.dehaven@gmail.com", "shana.hamilton11@gmail.com", "sarahjanebach@gmail.com", "niftifer@yahoo.com", "jillisawesome@gmail.com", "ps.143.10.sesiad@gmail.com", "smosby20@icloud.com", "yineria@hotmail.com", "elizabethhauenstein21@gmail.com", "apgal352@gmail.com", "tinkerbell1746@hotmail.com", "kiowastar7@aol.com", "ashley.handwork@gmail.com", "naobeni@yahoo.com", "superkulejenta@yahoo.com", "kaitlinveit@gmail.com", "jenniferdurst09@gmail.com", "oneisabigail@gmail.com", "inky257@gmail.com", "calleigh.j.kruse@gmail.com", "lizibeth@gmail.com", "avanti.b009@gmail.com", "amyannherbaugh@gmail.com", "17.alexa.clark@gmail.com", "danina.nieves@gmail.com", "silasandcassia@gmail.com", "stephaniekmunday@yahoo.com", "mrsholliebeakley@gmail.com", "abigaillawrenceshaw@gmail.com", "sgoodw21@gmail.com", "purplecheezits@gmail.com", "nadinetazmin@gmail.com", "sarahrhinchman@gmail.com", "megan0813@yahoo.com", "tashasamons@gmail.com", "marienageary@gmail.com", "button1207@hotmail.com", "mckennaleague@gmail.com", "alicia.g.holmes@gmail.com", "georgieann35@gmail.com", "gracetowery@gmail.com", "metzliiduran@gmail.com", "leslieanne1962@icloud.com", "wereworlddesigns@gmail.com", "lili.equihua@gmail.com", "amajgar20@gmail.com", "mpereiraburgos@gmail.com", "rachaelexie@gmail.com", "hoytdanae@gmail.com", "liztorres2024@icloud.com", "tarasdukes@gmail.com", "mccartymeg@gmail.com", "carmenwilkerson92@gmail.com", "jrepple@outlook.com", "j.leitnerjp13@gmail.co", "mlwynia10@gmail.com", "jessicalemeza@gmail.com", "tractorbee@gmail.com", "ashleigh.david@gmail.com", "earlylaura444@gmail.com", "abundanceofjoy@bellsouth.net", "meagan.arrott@gmail.com", "cara.wade947@gmail.com", "shelbydanielle321@gmail.com", "a.little.librarian@gmail.com", "hillward13.hw@gmail.com", "faketyfakefaketyfake@gmail.com", "travis@gastdesign.com", "alexandra.casser@gmail.com", "joahartistry@gmail.com", "e_mitcham0821@email.campbell.edu", "amliston@gmail.com", "mamaskitchen727@gmail.com", "mstebs94@gmail.com", "kellie1127@gmail.com", "lucasam28@gmail.com", "jessicarichmond00@gmail.com", "lien.day516@gmail.com", "emailmestuffdude@gmail.com", "yuliaabock@gmail.com", "oberst.megan@outlook.com", "rmburk1@gmail.com", "pearl.turnbow@gmail.com", "legomenaleathia@gmail.com", "carmencook5314@hotmail.com", "nawilko5@gmail.com", "pmurtoff@gmail.com"]
profiles=[{"type":"profile","attributes":{"email":e,"location":{"country":"United States"}}} for e in EMAILS]
body={"data":{"type":"profile-bulk-import-job","attributes":{"profiles":{"data":profiles}}}}
req=urllib.request.Request("https://a.klaviyo.com/api/profile-bulk-import-jobs/",
    data=json.dumps(body).encode(), method="POST")
req.add_header("Authorization","Klaviyo-API-Key "+KEY)
req.add_header("revision",REV)
req.add_header("Content-Type","application/json")
req.add_header("accept","application/json")
try:
    with urllib.request.urlopen(req) as r:
        d=json.loads(r.read().decode() or "{}")
        jid=d.get("data",{}).get("id","?")
        print("OK  status", r.status, " import job id:", jid)
        print("   ", len(EMAILS), "profiles queued for United States.")
except urllib.error.HTTPError as e:
    print("ERROR", e.code); print(e.read().decode()[:600])
