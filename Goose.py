"""
Desktop Goose-style prank. Windows only, standard library only.

Run:      python goose.py
Options:  --no-mouse       goose never touches your cursor
          --no-minimize    goose never minimizes or shoves your windows
          --chaos N        1 = gentle, 2 = default, 3 = full goose
          --no-edge        goose never opens Edge / types searches
          --no-poop        goose keeps its business to itself
          --no-context     goose ignores what you're doing (no app/idle/mouse reactions)
          --no-hud         no scoreboard, achievement banners or confetti
          --no-drag        the goose can't be picked up and thrown
          --click-release  clicking breaks the goose's grip on your mouse (off by default)
          --stage N        lock the goose's mood: 1 friendly, 2 condescending, 3 enemy, 4 unhinged
                           (0 = it escalates on its own, the default)
          --minutes N      auto-quit after N minutes (default 30; 0 = never)
          --sound          goose honks (system beep)
          --sprite FILE    use your own PNG (facing right) instead of the drawn goose
          --sprite-shrink N  shrink that PNG by N (default 2)

PANIC KEYS: Ctrl+Shift+Q or Ctrl+Alt+Q quit instantly and put everything back.
Safety notes:
  * Nothing is ever deleted. The "trash" is a drawing on a transparent overlay.
  * Minimized windows are restored automatically (and on exit).
  * Clicks do nothing while the goose is busy. Only with --click-release does a click make it let go.
    (Every steal/bin/wobble has a time limit, and the panic keys always work.)
  * No persistence, no network, no hidden processes.
Only run this on a machine where you have permission.

Context awareness: the goose looks only at the CATEGORY of your active app (picked from its
window title, held in memory for that one check), how long you've been idle, and how you're
moving/clicking the mouse. It never reads keystrokes, text or page content, and titles are
never shown, logged, stored or sent anywhere. --no-context switches all of it off.
"""
import argparse, atexit, ctypes, faulthandler, math, os, random, subprocess, sys, threading, time
import collections
import re
import traceback
import ctypes.wintypes as wt
import tkinter as tk

if sys.platform != "win32":
    sys.exit("This goose only runs on Windows.")

# ----------------------------------------------------------------- tuning ---
STEAL_SECS = (5, 8)      # how long the goose holds your mouse
TRASH_SECS = 5           # how long your mouse stays "deleted"
MINIMIZE_RESTORE = (15, 30)  # seconds before a minimized window comes back
INK_LIFETIME = 9         # seconds before scribbles vanish
GRACE_SECS = 15          # goose only wanders for this long after launch
FPS_MS = 16
KEY = "#010101"          # transparent colour key

TAUNTS = ["honk.", "nice tabs.", "HONK", "I'm helping.", "you're welcome.",
          "whatcha doing?", "don't mind me", "very productive!", "hm.",
          "that looked important", "have you tried Ctrl+Z?"]
BOARD_TEXTS = ["Hello. I'm busy being\nmore productive than you.",
               "Your unsaved work says\nhi. It's nervous.",
               "Have you tried turning\nyourself off and on again?",
               "Honk if you want me\nto stop. (I won't.)",
               "Meeting at 3.\nAgenda: goose."]

SEARCHES = ["how to get rid of a goose",
            "why is there a goose on my screen",
            "is it normal for a goose to live in my computer",
            "goose vs swan who wins",
            "can geese use computers",
            "how to apologise to a goose",
            "how to delete a goose",
            "is my computer haunted by a bird"]
CREATE_NO_WINDOW = 0x08000000
IDLE_FRAMES = (30, 150)       # pause between pranks (frames, ~60 per second)
SMEAR_CHANCE = 0.35           # chance a scrub smears the poop instead of cleaning it
MAX_POOPS = 12
POOP_HP = 3                   # scrubs (clicks) needed to clear a fresh pile
POOP, POOP_D, POOP_L = "#5b3a1e", "#3d2410", "#7a5230"
HEAT_RATE = 1.5               # how fast the goose's mood escalates with time
SPEAK_GAP = [25, 16, 10, 6]   # min seconds between unprompted comments, per stage
BUBBLE_STYLE = [("white", "black", 11), ("#fff6cf", "#8a6d00", 11),
                ("#ffd9d6", "#b00020", 12), ("#ecd9ff", "#6a00b8", 13)]
CHASE_SPEED = 8               # px per frame while chasing your live cursor
DRAG_SPEED = 4.5              # px per frame while hauling it somewhere
CHASE_GIVEUP = 90             # seconds before the goose admits defeat
MAX_MINIMIZED = 3
WEIGHTS = [40, 15, 15, 8, 10, 8, 8, 6, 6, 6, 4, 4, 6, 3]
# wander, scribble, minimize, whiteboard, steal, trash, search, shove, chase, poop, wobble, stalk, graffiti, decoys
CHAOS = {
    1: dict(GRACE_SECS=15, IDLE_FRAMES=(30, 150), INK_LIFETIME=9, STEAL_SECS=(5, 8), TRASH_SECS=5,
            MAX_MINIMIZED=3, WEIGHTS=[40, 15, 15, 8, 10, 8, 8, 6, 6, 6, 4, 4, 6, 3]),
    2: dict(GRACE_SECS=8, IDLE_FRAMES=(15, 70), INK_LIFETIME=14, STEAL_SECS=(8, 14), TRASH_SECS=7,
            MAX_MINIMIZED=4, WEIGHTS=[15, 18, 15, 10, 14, 10, 10, 10, 12, 10, 8, 8, 10, 6]),
    3: dict(GRACE_SECS=3, IDLE_FRAMES=(0, 25), INK_LIFETIME=25, STEAL_SECS=(12, 20), TRASH_SECS=10,
            MAX_MINIMIZED=6, WEIGHTS=[3, 20, 15, 12, 16, 12, 12, 14, 16, 14, 12, 10, 14, 10]),
}


for _lv, _cs, _ds, _sm, _mp, _hr in ((1, 5, 3, 0.25, 8, 1.0), (2, 8, 4.5, 0.35, 12, 1.5),
                                       (3, 12, 6, 0.5, 18, 2.5)):
    CHAOS[_lv].update(CHASE_SPEED=_cs, DRAG_SPEED=_ds, SMEAR_CHANCE=_sm, MAX_POOPS=_mp, HEAT_RATE=_hr)


def apply_chaos(level):
    globals().update(CHAOS[level])
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "goose_log.txt")


def log(msg):
    """Print and append to goose_log.txt next to the script."""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass

# Stage-by-stage search taunts (the goose types one of these into Edge).
SEARCHES_BY_STAGE = {
    1: SEARCHES,
    2: ["how to be more productive", "why can't I focus", "how to stop procrastinating",
        "signs you are being judged by a goose", "how to get a goose to like you"],
    3: ["how to stop a goose from winning", "why do I keep losing to a goose", "can you sue a goose",
        "goose vs human who wins", "how to apologise to a goose"],
    4: ["how to become a goose king", "is a goose legally a god", "how to rule a desktop",
        "goose supremacy", "the goose cannot be stopped"],
}

# DIALOGUE[context] = [stage1 lines, stage2 lines, stage3 lines, stage4 lines]
# Placeholders: {cleaned} {escapes} {tries} {mins}
DIALOGUE = {
    "intro": [["hello. don't mind me.", "just a goose. nothing to see here."],
              ["back again? bold choice."],
              ["round two. I've been practising."],
              ["BEHOLD. YOUR KING HAS ARRIVED."]],
    "stage_up": [["..."],
                 ["hm. I'm starting to have concerns about you.", "I've been watching. we need to talk."],
                 ["okay. I'm done being polite.", "you've made this personal."],
                 ["I AM INEVITABLE.", "THE GOOSE ASCENDS. BOW."]],
    "idle": [["honk.", "just vibing.", "very productive!", "don't mind me.", "hm."],
             ["is that really the best use of your time?", "interesting choices today.",
              "I'm not judging. (I'm judging.)", "bold strategy."],
             ["you can't make me leave.", "keep trying. it's adorable.", "I live here now.",
              "your control panel is a joke."],
             ["I HAVE WON. YOU JUST HAVEN'T NOTICED.", "THE PROPHECY WAS ABOUT ME.",
              "I SEE ALL. I HONK ALL.", "THE COMPUTER IS MY KINGDOM."]],
    # ---- what app you're in
    "app_browser": [["ooh, internet.", "found anything interesting?"],
                    ["that's the tab you chose? hm.", "how many tabs is too many? you tell me."],
                    ["go ahead. search 'how to stop a goose'. I'll wait.", "your browser belongs to me now."],
                    ["THE INTERNET BOWS TO ME.", "SEARCH ALL YOU WANT. I AM THE ALGORITHM."]],
    "app_office": [["working hard?", "nice document."],
                   ["did you save that? you didn't, did you.", "that font choice is a decision."],
                   ["Ctrl+S won't save you from me.", "nice document. shame if it got honked on."],
                   ["MY DOCUMENT NOW. 'GOOSE REPORT Q4.'", "I AM THE FOOTNOTE. I AM THE APPENDIX."]],
    "app_code": [["ooh, code.", "is that a bug? or a feature?"],
                 ["a goose could have written that. a smarter goose.", "have you tried reading the error?"],
                 ["I'm going to cause your next bug.", "your linter is on my side."],
                 ["I AM THE BUG. I HAVE ALWAYS BEEN THE BUG.", "THE COMPILER SERVES ME NOW."]],
    "app_chat": [["chatting? say hi from me.", "who are you talking to?"],
                 ["are you sure you should send that?", "that reply took a while to write. hm."],
                 ["tell them the goose says hi. and that you lost.", "I'm reading over your shoulder. (I can't read.)"],
                 ["TELL THEM I'M WINNING.", "SEND MY DEMANDS. ALL OF THEM."]],
    "app_media": [["are we watching goose videos?", "ooh. snacks?"],
                  ["is this really what you're doing right now?", "one more episode, right."],
                  ["pause it. I want attention.", "I'm blocking the good part. on purpose."],
                  ["I AM THE MAIN CHARACTER NOW.", "ROLL THE CREDITS. THE GOOSE WON."]],
    "app_game": [["ooh, a game.", "player two has entered."],
                 ["you're not very good at this, are you.", "this seems like a bad idea."],
                 ["lag? that's me. I'm the lag.", "I'm better at this than you."],
                 ["MY GAME. MY RULES. HONK.", "I AM THE FINAL BOSS."]],
    "app_files": [["tidying up?", "so many files."],
                  ["that folder structure is a cry for help.", "'final_final2'? really?"],
                  ["rearrange things. go on. I'll watch.", "I could reorganise it. worse."],
                  ["ALL FILES BELONG TO THE GOOSE.", "I RENAME YOU ALL 'HONK.DOC'."]],
    "app_settings": [["looking for the goose setting?", "no goose setting in there."],
                     ["you won't find me in there.", "there's no off switch for charm."],
                     ["go ahead, search settings for 'goose'.", "nothing in there can touch me."],
                     ["SETTINGS CANNOT CONTAIN ME.", "I AM BEYOND CONTROL PANELS."]],
    "app_ai": [["is that an AI? tell it I said hi.", "asking a robot for help?"],
               ["asking a robot how to deal with a goose? how modest.", "I hope it gives better advice than you."],
               ["it can't help you. I've already won.", "tell your robot I'm unbeatable."],
               ["YOUR ROBOT SERVES ME TOO.", "EVEN THE MACHINES FEAR THE GOOSE."]],
    "app_mail": [["email? thrilling.", "any goose mail?"],
                 ["reply all? bold.", "that inbox needs help."],
                 ["send them a photo of me. I insist.", "I'm CC'd. always."],
                 ["I AM THE UNREAD COUNT.", "ALL EMAILS ARE GOOSE EMAILS NOW."]],
    "app_desktop": [["staring at the desktop? me too.", "ooh, wallpaper."],
                    ["nothing to do? let me help.", "an empty desktop. how brave."],
                    ["empty desktop. perfect stage for me.", "nowhere to hide."],
                    ["MY THRONE ROOM.", "THE DESKTOP IS MY KINGDOM."]],
    # ---- how you're behaving
    "late_night": [["it's very late. go to sleep?"], ["it's the middle of the night. this is a decision."],
                   ["sleep is for the weak. I'm awake."], ["THE NIGHT BELONGS TO THE GOOSE."]],
    "lunch": [["lunchtime? snacks for me?"], ["still working at lunch? tragic."],
              ["skip lunch. I'm staying."], ["FEED THE GOOSE."]],
    "idle_user": [["hello? you there?"], ["asleep? good. I'll rearrange things."],
                  ["I can do whatever I want now."], ["THE KINGDOM IS MINE ALONE."]],
    "back": [["oh, you're back."], ["took you long enough."], ["missed me?"], ["THE SUBJECT RETURNS."]],
    "many_windows": [["so many windows."], ["that's a lot of windows. very organised."],
                     ["I can minimise those, you know."], ["I'LL TAKE THE LOT."]],
    "rage_click": [["easy there."], ["clicking harder won't help."],
                   ["click. click. click. I'm laughing."], ["YES. FEEL THE RAGE. I FEED ON IT."]],
    "mouse_frantic": [["whoa. calm down."], ["that's a lot of mouse for no reason."],
                      ["wiggle all you want. I'm not leaving."], ["DANCE, MORTAL."]],
    "window_hopping": [["making up your mind?"], ["so decisive."],
                       ["looking for an escape? there isn't one."], ["FLEE TO ANY WINDOW. I AM THERE."]],
    "dodge": [["nope."], ["can't catch me."], ["you can't touch me. fascinating."],
              ["MORTALS CANNOT TOUCH GREATNESS."]],
    "click_near": [["no clicking the goose."], ["that click was adorable."],
                   ["you clicked me? you missed."], ["YOUR CLICKS ARE A PRAYER. UNANSWERED."]],
    # ---- reactions to you fighting back
    "escape": [["...you win this time."], ["lucky. don't get used to it."],
               ["running? fine. I'll find you."], ["YOU RUN. I ALWAYS ARRIVE."]],
    "release": [["fine. take it."], ["impatient, aren't we."], ["clicking to escape? cute."],
                ["YOU CANNOT ESCAPE BY CLICKING."]],
    "poop_clean": [["rude.", "spotless. boring."], ["{cleaned} down. many more to go.", "tidy. predictable."],
                   ["clean it all you like. I make more.", "{cleaned} cleaned. {cleaned} more coming."],
                   ["YOUR CLEANING FEEDS ME.", "{cleaned}. COUNT THEM. I'M NOT STOPPING."]],
    "poop_milestone": [["{cleaned} poops cleaned. impressive."], ["{cleaned} cleaned. should I be worried?"],
                       ["{cleaned} and you still haven't won."], ["{cleaned}. A FEW MORE FOR THE TREASURY."]],
    "poop_smear": [["oops. smeared."], ["that made it worse."], ["you made it worse. all you."],
                   ["NOW IT'S ART. MY ART."]],
    # ---- the pranks themselves
    "steal_start": [["mine."], ["I'll be needing that."], ["give me that."], ["YIELD."]],
    "steal_end": [["thanks. honk."], ["see? safer with me."], ["I'll have it again soon."],
                  ["I GIVE. I TAKE. I AM GOD."]],
    "trash_start": [["clean-up time."], ["let me tidy that for you."], ["into the bin you go."],
                    ["TO THE VOID WITH YOU."]],
    "trash_delete": [["Cursor.exe deleted."], ["Cursor.exe deleted. productivity improved."],
                     ["Cursor.exe deleted. you can't stop me."], ["CURSOR.EXE HAS BEEN SACRIFICED."]],
    "trash_back": [["it's back. probably."], ["it's back. you're welcome."], ["back. for now."],
                   ["I RETURN WHAT I WISH."]],
    "chase_start": [["get over here."], ["let's play a game."], ["run. I like it when you run."],
                    ["RUN, MORTAL. RUN."]],
    "fling": [["yoink.", "wrong way."], ["that's not where you wanted it, is it."], ["go get it."], ["FETCH."]],
    "poop_drop": [["oops.", "don't look."], ["you'll want to clean that.", "I assume you'll deal with it."],
                  ["clean it. I dare you.", "fresh. for you."], ["A GIFT. FOR MY SUBJECT.", "WORSHIP THE POOP."]],
    "scribble": [["art."], ["you wouldn't understand."], ["this is what I think of your wallpaper."],
                 ["BEHOLD. MY MASTERPIECE."]],
    "minimize": [["hide."], ["you don't need that."], ["you weren't using that. now you're not."],
                 ["BEGONE, WINDOW."]],
    "minimize_office": [["that wasn't saved, was it?"], ["that needed saving, didn't it."],
                        ["I'll hold your document hostage."], ["YOUR DOCUMENT NOW LIVES IN THE TASKBAR REALM."]],
    "minimize_code": [["bugs need a break too."], ["you'll thank me. maybe."],
                      ["I'm helping. your code is better when I can't see it."], ["THE COMPILER CAN WAIT."]],
    "minimize_chat": [["they can wait."], ["they can wait. you can't, apparently."],
                      ["ignore them. focus on me."], ["YOUR FRIENDS ARE MINE NOW."]],
    "minimize_media": [["I'm hiding the fun."], ["back to work."], ["no more videos. only goose."],
                       ["ONLY THE GOOSE MAY ENTERTAIN."]],
    "minimize_game": [["pause. goose time."], ["probably a good time to quit."], ["you were losing anyway."],
                      ["GAME OVER. THE GOOSE WINS."]],
    "shove_grab": [["mine now."], ["let me help you reorganise."], ["I'm moving this. try to stop me."],
                   ["I TAKE THIS WINDOW. IT IS MINE."]],
    "shove": [["better here.", "feng shui."], ["you're welcome. it looks better."],
              ["I like it better where I put it."], ["I REARRANGE REALITY."]],
    "search_start": [["ooh, internet."], ["let me show you how it's done."], ["watch and learn."],
                     ["I SHALL CONSULT THE ORACLE."]],
    "search_interrupted": [["rude. I was typing."], ["do you mind? I was typing."],
                           ["you interrupted me. you'll regret that."], ["YOU DARE INTERRUPT THE ORACLE."]],
    "search_done": [["0 geese found. suspicious."], ["not a single helpful result."],
                    ["even Bing agrees I'm right."], ["THE INTERNET CONFIRMS: I AM SUPREME."]],
    "whiteboard_end": [["honk."], ["think about it."], ["read it again. slowly."],
                       ["THE PROPHECY HAS BEEN POSTED."]],
    "board": [["Hello. I'm busy being\nmore productive than you.", "Meeting at 3.\nAgenda: goose."],
              ["You've been at this for\n{mins} min. Is it working?", "Poops cleaned so far: {cleaned}.\nThat's a choice."],
              ["Attempts to stop me: {tries}\nSuccessful: 0", "Escapes: {escapes}\nGoose wins: all of them."],
              ["I AM THE GOOSE.\nTHE GOOSE IS THE DESKTOP.\nTHE DESKTOP IS ME.",
               "WINS: INFINITE\nLOSSES: NONE\nTHANK YOU FOR PLAYING"]],
}

DIALOGUE.update({
    "gloat": [["that's {score} to nothing. just saying."], ["{score}-0. not that I'm counting. (I'm counting.)"],
              ["GOOSE {score}. YOU 0. IMAGINE."], ["{score} TO ZERO. THE CROWD GOES WILD."]],
    "psych": [["just kidding."], ["did you really think I'd stop?"], ["fooled you. again."],
              ["HAHAHA. THE GOOSE DECEIVES."]],
    "wobble_start": [["steady hands?"], ["let's see how steady your hands are."], ["good luck clicking things."],
                     ["TREMBLE, MORTAL."]],
    "wobble_end": [["steady as a rock. oh wait."], ["that was a bit shaky, wasn't it."],
                   ["you're welcome for the extra challenge."], ["I AM THE EARTHQUAKE."]],
    "stalk_start": [["I'm just walking this way. coincidence."], ["don't mind me. just following."],
                    ["I go where you go. forever."], ["I AM YOUR SHADOW. I AM YOUR DOOM."]],
    "stalk_end": [["see you later. (I won't leave.)"], ["bored now."], ["that was fun for me."],
                  ["I SHALL RETURN. I NEVER LEFT."]],
    "decoys_start": [["I have friends."], ["I have friends now."], ["there's more of me. don't ask how."],
                     ["BEHOLD MY HONKING LEGION."]],
    "decoys_mid": [["can you spot the real one?"], ["can you spot the real one? (you can't.)"],
                   ["one of us is real. the rest are better."], ["ONE IS REAL. ALL ARE TERRIFYING."]],
    "graffiti": [["HONK"], ["TRY HARDER", "NICE TRY"], ["L", "SKILL ISSUE", "GOOSE > YOU"],
                 ["I WIN", "BOW", "GOOSE FOREVER"]],
})

# Extra lines appended to existing contexts, one list per stage.
_EXTRA = {
    "idle": [["I'm not following you. I'm accompanying you.", "have you considered more honk?",
              "you have a goose now. congratulations."],
             ["that's a lot of effort for so little result.", "I'd say good luck, but I've seen your progress.",
              "you could have finished by now. just saying.", "I've seen faster clicking from a snail."],
             ["tell me again how you're in control.", "you've spent {mins} minutes losing to a goose.",
              "every click you make is a gift to me.", "your mouse is a suggestion. I decide.",
              "I have a score and you do not."],
             ["I HAVE EATEN YOUR PRODUCTIVITY AND IT WAS DELICIOUS.", "KNEEL BEFORE THE FEATHERED ONE.",
              "I HAVE BEEN HERE SINCE BEFORE THE FIRST WINDOW.", "YOU CANNOT UNDERSTAND ME. YOU CAN ONLY HONK."]],
    "escape": [["huh. you're quick."], ["that was luck. I'm sure of it.", "enjoy it while it lasts."],
               ["running only makes the chase more fun.", "that's one point to nobody."],
               ["I LET YOU ESCAPE. OBVIOUSLY.", "THAT WAS A TACTICAL RETREAT. MINE."]],
    "release": [["okay okay."], ["so impatient."],
                ["clicking won't save you. you're on {tries} attempts.", "that counts as a surrender. I'm counting it."],
                ["I LET YOU GO. ON PURPOSE. I'M BENEVOLENT."]],
    "steal_start": [["finders keepers."], ["you won't mind. you weren't using it."],
                    ["relax. you're going to love this."], ["YOUR MOUSE IS MY SCEPTRE NOW."]],
    "poop_clean": [[], ["tidy. predictable.", "you missed a spot."], ["you missed a spot.", "that's {cleaned}. I'm just warming up."],
                   ["{cleaned} CLEANED. I'VE ALREADY PLANNED {cleaned} MORE."]],
    "poop_drop": [["oh no. how did that get there."], ["oh dear. what a mess. whose fault is it, hm?"],
                  ["I made this for you. free labour."], ["MY MASTERPIECE. DO NOT TOUCH. (TOUCH IT.)"]],
    "trash_delete": [[], ["Cursor.exe deleted. you're welcome."], ["Cursor.exe deleted. have fun clicking."],
                     ["CURSOR.EXE HAS ASCENDED TO THE BIN REALM."]],
    "app_browser": [[], ["so many tabs. you must be very organised."], ["I can see your tabs. all of them. judge judge judge."],
                    ["EVERY TAB IS A GOOSE TAB NOW."]],
    "app_code": [[], ["that's... a choice of variable name."], ["your code compiles because I allow it."],
                 ["I WRITE THE BUGS. YOU JUST FIND THEM."]],
    "app_chat": [[], ["typing... stop typing... typing again... hm."], ["go on, tell them how it's going. I'll wait."],
                 ["TELL THEM THE GOOSE ISN'T LEAVING."]],
}
for _k, _per_stage in _EXTRA.items():
    for _i, _more in enumerate(_per_stage):
        DIALOGUE[_k][_i].extend(_more)

# Lines for being picked up: FIVE tiers, indexed by how many times you've grabbed the goose.
DIALOGUE.update({
    "grab": [["hey!", "excuse me?", "hands off the feathers."],
             ["again?!", "I said no touching.", "that's twice."],
             ["STOP THAT.", "I will remember this.", "third time. I'm keeping count."],
             ["YOU ARE MAKING A MISTAKE.", "I AM TRYING TO STAY CALM.", "{grabs}. THAT'S {grabs}."],
             ["THAT IS IT.", "YOU HAVE AWOKEN THE GOOSE.", "I WILL END YOUR MOUSE."]],
    "dragging": [["put me down.", "this is undignified.", "honk?!"],
                 ["PUT. ME. DOWN.", "I'm filing a complaint.", "I'm getting dizzy."],
                 ["I'LL BITE.", "you'll regret every second.", "HONK HONK HONK"],
                 ["THIS IS ASSAULT.", "I'M GOING TO RUIN YOUR WHOLE DAY.", "UNHAND ME, MORTAL."],
                 ["HONKHONKHONKHONK", "YOUR MOUSE IS GOING IN THE BIN.", "I WILL REMEMBER THIS FOREVER."]],
    "dropped": [["rude.", "ow."],
                ["you dropped me. ON PURPOSE.", "that's assault."],
                ["oh, you're going to regret that.", "you'll pay for that."],
                ["THE REVENGE BEGINS NOW.", "YOU HAVE SIGNED YOUR OWN FATE."],
                ["IT'S GOOSE TIME.", "I'M GOING TO ENJOY THIS."]],
    "slip": [["that's enough. thank you."], ["okay, I'm leaving. you can't hold me."],
             ["I'M DONE BEING HELD."], ["NO MORE. I BREAK FREE."], ["I BREAK ALL CHAINS. HONK."]],
    "revenge": [["okay. my turn."], ["my turn. you'll like this."], ["time for payback."],
                ["PAYBACK. NOW. FOR EVERYTHING."], ["EVERYTHING YOU LOVE WILL BE HONKED UPON."]],
    "grab_grudge": [["I haven't forgotten you picked me up. {grabs} times."],
                    ["you've picked me up {grabs} times. I'm keeping notes."],
                    ["{grabs} grabs. I'm building a case."],
                    ["{grabs} GRABS. THE TRIBUNAL WILL BE SWIFT."]],
})
DIALOGUE["board"][2].append("Times you grabbed me: {grabs}\nTimes I forgave you: 0")
DIALOGUE["board"][3].append("GRABS: {grabs}\nFORGIVENESS: NONE")

# Which app category a window title belongs to (checked in this order). Matching is local and
# in-memory; the title itself is never stored or shown.
CATEGORY_KEYWORDS = [
    ("ai", ("claude", "chatgpt", "gemini", "copilot", "perplexity")),
    ("game", ("steam", "epic games", "minecraft", "fortnite", "valorant", "roblox",
              "league of legends", "counter-strike")),
    ("chat", ("discord", "slack", "microsoft teams", "whatsapp", "messenger", "telegram", "signal", "zoom")),
    ("media", ("youtube", "netflix", "twitch", "spotify", "vlc", "prime video", "disney+", "hulu")),
    ("mail", ("outlook", "gmail", "inbox")),
    ("code", ("visual studio", "vs code", "pycharm", "intellij", "sublime", "notepad++", "cmd",
              "powershell", "windows terminal", "command prompt", "github", "stack overflow",
              "jupyter", "python")),
    ("office", ("word", "excel", "powerpoint", "google docs", "google sheets", "google slides",
                "onenote", "libreoffice", ".docx", ".xlsx", ".pptx")),
    ("settings", ("settings", "control panel", "task manager")),
    ("browser", ("edge", "chrome", "firefox", "brave", "opera", "safari")),
]


def _kw_re(words):
    parts = [(r"\b" + re.escape(w) + r"\b") if (w[0].isalnum() and w[-1].isalnum()) else re.escape(w)
             for w in words]
    return re.compile("|".join(parts))


CATEGORY_PATTERNS = [(cat, _kw_re(words)) for cat, words in CATEGORY_KEYWORDS]

# ------------------------------------------------------------- win32 setup ---
user32 = ctypes.windll.user32
dwm = ctypes.windll.dwmapi
user32.SetProcessDPIAware()
H = wt.HWND
user32.IsWindowVisible.argtypes = [H]
user32.IsIconic.argtypes = [H]
user32.ShowWindow.argtypes = [H, ctypes.c_int]
user32.GetWindowRect.argtypes = [H, ctypes.c_void_p]
user32.GetWindowTextLengthW.argtypes = [H]
user32.GetWindowThreadProcessId.argtypes = [H, ctypes.c_void_p]
user32.GetClassNameW.argtypes = [H, ctypes.c_void_p, ctypes.c_int]
user32.GetWindowLongW.argtypes = [H, ctypes.c_int]
user32.SetWindowLongW.argtypes = [H, ctypes.c_int, ctypes.c_long]
user32.GetParent.argtypes = [H]
user32.GetParent.restype = H
user32.CreateCursor.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
user32.CreateCursor.restype = ctypes.c_void_p
user32.CopyIcon.argtypes = [ctypes.c_void_p]
user32.CopyIcon.restype = ctypes.c_void_p
user32.SetSystemCursor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
dwm.DwmGetWindowAttribute.argtypes = [H, wt.DWORD, ctypes.c_void_p, wt.DWORD]
user32.GetWindowTextW.argtypes = [H, ctypes.c_void_p, ctypes.c_int]
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = H
user32.SetForegroundWindow.argtypes = [H]
user32.GetForegroundWindow.restype = H
user32.SendInput.argtypes = [wt.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SetWindowPos.argtypes = [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.IsZoomed.argtypes = [H]
user32.IsWindow.argtypes = [H]

SW_W, SW_H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
CURSOR_IDS = (32512, 32513, 32514, 32515, 32516, 32642, 32643, 32644, 32645,
              32646, 32648, 32649, 32650)
_cursor_hidden = False


def get_cursor():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def set_cursor(x, y):
    user32.SetCursorPos(int(max(0, min(SW_W - 1, x))), int(max(0, min(SW_H - 1, y))))


def hide_cursor():
    global _cursor_hidden
    if _cursor_hidden:
        return
    and_mask = (ctypes.c_ubyte * 128)(*([0xFF] * 128))
    xor_mask = (ctypes.c_ubyte * 128)(*([0x00] * 128))
    blank = user32.CreateCursor(None, 0, 0, 32, 32, and_mask, xor_mask)
    for cid in CURSOR_IDS:
        user32.SetSystemCursor(user32.CopyIcon(blank), cid)
    _cursor_hidden = True


def show_cursor():
    global _cursor_hidden
    # SPI_SETCURSORS: reload the user's real cursors from the registry
    user32.SystemParametersInfoW(0x0057, 0, None, 0)
    _cursor_hidden = False


atexit.register(show_cursor)


def mouse_clicked():
    return bool(user32.GetAsyncKeyState(0x01) & 0x8000 or user32.GetAsyncKeyState(0x02) & 0x8000)


def panic_pressed():
    k = user32.GetAsyncKeyState
    return bool(k(0x11) & 0x8000 and k(0x51) & 0x8000 and
                (k(0x10) & 0x8000 or k(0x12) & 0x8000))


EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def visible_windows():
    out, own = [], os.getpid()

    def cb(h, _):
        if not user32.IsWindowVisible(h) or user32.IsIconic(h):
            return True
        if user32.GetWindowTextLengthW(h) == 0:
            return True
        if user32.GetWindowLongW(h, -20) & 0x80:      # tool windows
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == own:
            return True
        cloaked = ctypes.c_int(0)
        dwm.DwmGetWindowAttribute(h, 14, ctypes.byref(cloaked), 4)
        if cloaked.value:
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, buf, 256)
        if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return True
        r = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        if r.right - r.left < 250 or r.bottom - r.top < 150:
            return True
        out.append((h, r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


# ------------------------------------------------- keyboard / Edge / bin helpers ---
class _KI(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MI(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _IU(ctypes.Union):
    _fields_ = [("ki", _KI), ("mi", _MI)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _IU)]


def _send(*events):
    arr = (_INPUT * len(events))(*events)
    user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))


def _vk(vk, up=False):
    return _INPUT(1, _IU(ki=_KI(vk, 0, 2 if up else 0, 0, 0)))


def _uni(ch, up=False):
    return _INPUT(1, _IU(ki=_KI(0, ord(ch), 4 | (2 if up else 0), 0, 0)))


def send_ctrl_l():
    _send(_vk(0x11), _vk(0x4C), _vk(0x4C, True), _vk(0x11, True))


def send_enter():
    _send(_vk(0x0D), _vk(0x0D, True))


def send_char(ch):
    _send(_uni(ch), _uni(ch, True))


def edge_windows():
    out = []

    def cb(h, _):
        if not user32.IsWindowVisible(h):
            return True
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(h, buf, 128)
        if buf.value != "Chrome_WidgetWin_1":
            return True
        n = user32.GetWindowTextLengthW(h)
        if n == 0:
            return True
        t = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, t, n + 1)
        if "Edge" in t.value:
            out.append(h)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


# Read-only lookup of the desktop "Recycle Bin" icon position via Windows UI Automation.
PS_FIND_BIN = r"""
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public class D { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
[void][D]::SetProcessDPIAware()
Add-Type -AssemblyName UIAutomationClient
$ae = [System.Windows.Automation.AutomationElement]
$root = $ae::RootElement
$name = New-Object System.Windows.Automation.PropertyCondition($ae::NameProperty, 'Recycle Bin')
foreach ($cls in 'Progman', 'WorkerW') {
  $c = New-Object System.Windows.Automation.PropertyCondition($ae::ClassNameProperty, $cls)
  foreach ($top in $root.FindAll([System.Windows.Automation.TreeScope]::Children, $c)) {
    $el = $top.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $name)
    if ($el) {
      $r = $el.Current.BoundingRectangle
      if ($r.Width -gt 0) { "{0},{1}" -f [int]($r.X + $r.Width / 2), [int]($r.Y + $r.Height / 2); exit }
    }
  }
}
"""


def find_desktop_bin():
    """Return (x, y) of the real Recycle Bin icon, or None."""
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_FIND_BIN],
                             capture_output=True, text=True, timeout=12,
                             creationflags=CREATE_NO_WINDOW)
        import re as _re
        m = _re.search(r"(\d+),(\d+)", res.stdout or "")
        if m:
            x, y = int(m.group(1)), int(m.group(2))
            if 0 <= x < SW_W and 0 <= y < SW_H:
                return x, y
    except Exception:
        pass
    return None


DESKTOP_CLASSES = {"SysListView32", "SHELLDLL_DefView", "WorkerW", "Progman"}


def desktop_visible_at(x, y):
    """True if the desktop (not some app window) is what's at this point."""
    try:
        h = user32.WindowFromPoint(wt.POINT(int(x), int(y)))
        if not h:
            return False
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == os.getpid():          # our own click-through overlay
            return True
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(h, buf, 128)
        return buf.value in DESKTOP_CLASSES
    except Exception:
        return False


class _LII(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def idle_seconds():
    """Seconds since the last keyboard/mouse input (no information about WHAT was typed)."""
    try:
        li = _LII()
        li.cbSize = ctypes.sizeof(_LII)
        user32.GetLastInputInfo(ctypes.byref(li))
        return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 0.0


def window_category(h):
    """Rough category of a window ('browser', 'code', ...). The title is used only inside this
    function and is never stored, logged or shown."""
    try:
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(h, buf, 128)
        cls = buf.value.lower()
        if cls in ("progman", "workerw"):
            return "desktop"
        if cls == "cabinetwclass":
            return "files"
        n = user32.GetWindowTextLengthW(h)
        if not n:
            return "other"
        tb = ctypes.create_unicode_buffer(min(n, 255) + 1)
        user32.GetWindowTextW(h, tb, len(tb))
        title = tb.value.lower()
        for cat, rx in CATEGORY_PATTERNS:
            if rx.search(title):
                return cat
    except Exception:
        pass
    return "other"


def foreground_category():
    h = user32.GetForegroundWindow()
    if not h:
        return None, None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    if pid.value == os.getpid():
        return h, "goose"
    return h, window_category(h)


def ellipse_pts(cx, cy, rx, ry, ang=0.0, n=28):
    ca, sa = math.cos(ang), math.sin(ang)
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        pts += [cx + ex * ca - ey * sa, cy + ex * sa + ey * ca]
    return pts


ACHIEVEMENTS = [
    ("steal1", lambda g: g.counts.get("steals", 0) >= 1, "Mouse Mugged"),
    ("trash1", lambda g: g.counts.get("trashed", 0) >= 1, "Cursor.exe Has Left The Chat"),
    ("clean1", lambda g: g.stats["cleaned"] >= 1, "Janitor (Unpaid)"),
    ("clean10", lambda g: g.stats["cleaned"] >= 10, "Professional Poop Scooper"),
    ("smear3", lambda g: g.stats["smeared"] >= 3, "You Made It Worse"),
    ("escape3", lambda g: g.stats["escapes"] >= 3, "Fast Fingers (Doesn't Count)"),
    ("release3", lambda g: g.stats["releases"] >= 3, "Click Click (Futile)"),
    ("search1", lambda g: g.counts.get("searches", 0) >= 1, "Goose Googled Itself"),
    ("wobble1", lambda g: g.counts.get("wobbles", 0) >= 1, "Steady Hands (Not Really)"),
    ("stage3", lambda g: g.stage >= 3, "Personal Enemy Unlocked"),
    ("stage4", lambda g: g.stage >= 4, "The Goose Ascends"),
    ("ten_min", lambda g: time.time() - g.start > 600, "Still Here After 10 Minutes"),
    ("score25", lambda g: g.score >= 25, "25 Goose Points. You: Zero."),
]

ACHIEVEMENTS.extend([
    ("grab1", lambda g: g.grabs >= 1, "Laid Hands On The Goose"),
    ("grab5", lambda g: g.grabs >= 5, "Goose Abuser (Unlicensed)"),
])

# ------------------------------------------------------------------- goose ---
class Goose:
    def __init__(self, args):
        self.args = args
        self.root = tk.Tk()
        r = self.root
        r.overrideredirect(True)
        r.geometry(f"{SW_W}x{SW_H}+0+0")
        r.attributes("-topmost", True)
        r.config(bg=KEY)
        r.attributes("-transparentcolor", KEY)
        self.c = tk.Canvas(r, bg=KEY, highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        r.update_idletasks()
        hwnd = user32.GetParent(r.winfo_id()) or r.winfo_id()
        ex = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20 | 0x80)  # layered+click-through+toolwindow
        self.mode = None             # None | 'drag' | 'toss'
        self.drag = None
        self.vx = self.vy = 0.0
        self.toss_frames = 0
        self.grabs = 0
        self.pending_revenge = False
        self.hit = None
        self.hit_geo = ""
        self.hit_grabbable = None
        if not args.no_drag:
            # a near-invisible little window that follows the goose and catches clicks on it
            self.hit = tk.Toplevel(r)
            self.hit.overrideredirect(True)
            self.hit.attributes("-topmost", True)
            self.hit.attributes("-alpha", 0.01)
            self.hit.config(bg="white", cursor="fleur")
            self.hit.geometry("96x86+0+0")
            self.hit.bind("<ButtonPress-1>", self.on_press)
            self.hit.bind("<B1-Motion>", self.on_motion)
            self.hit.bind("<ButtonRelease-1>", self.on_release)

        self.x, self.y = SW_W * 0.5, SW_H * 0.5
        self.face, self.phase = 1, 0.0
        self.bubble = None           # [text, frames_left]
        self.ink = []                # (item_id, born)
        self.minimized = []          # [hwnd, restore_at]
        self.shoved = []             # [hwnd, orig_x, orig_y, restore_at]
        self.poops = []              # dicts, one small click-capturing window each
        self.stage = args.stage or 1
        self.grudge = 0.0            # goes up each time you fight back
        self.stats = {"cleaned": 0, "smeared": 0, "escapes": 0, "releases": 0}
        self.recent = collections.deque(maxlen=14)   # don't repeat yourself
        self.last_spoke = 0.0
        self.obs = {"t": time.time(), "cursor": get_cursor(), "path": 0.0, "frantic": 0,
                    "down": False, "clicks": collections.deque(), "switches": collections.deque(),
                    "fg": None, "cat": None, "cat_t": time.time(), "idle": 0.0,
                    "win_t": time.time(), "dodge_t": time.time(), "said": {}}
        self.score = 0               # the goose's points. yours stay at zero.
        self.counts = {}
        self.achieved = set()
        self.banners = collections.deque()
        self.banner = None
        self.confetti_list = []
        self.decoys = []
        self.cur = None
        self.idle = 60
        self.start = time.time()
        self.deadline = self.start + args.minutes * 60 if args.minutes > 0 else 0
        self.warned = False
        self.errors = 0
        self.cur_name = ''
        self.running = True
        self.bin_pos = None
        threading.Thread(target=self._scan_bin, daemon=True).start()
        self.sprite = {}
        if args.sprite:
            try:
                img = tk.PhotoImage(file=args.sprite)
                if args.sprite_shrink > 1:
                    img = img.subsample(args.sprite_shrink)
                flipped = tk.PhotoImage()
                self.root.tk.call(str(flipped), "copy", str(img), "-subsample", -1, 1)
                self.sprite = {1: img, -1: flipped, "w": img.width(), "h": img.height()}
            except Exception as e:
                print(f"Could not load sprite ({e}); using the built-in goose.")
        self.talk("intro", frames=200)

    # -- helpers
    def _scan_bin(self):
        self.bin_pos = find_desktop_bin()

    def reach_xy(self):
        """Offset from goose centre to its beak (x is mirrored by facing)."""
        if self.sprite:
            return 0.34 * self.sprite["w"], 0.20 * self.sprite["h"]
        return 26, 36

    def head(self):
        rx, ry = self.reach_xy()
        return self.x + self.face * rx, self.y - ry + math.sin(self.phase) * 1.5

    def say(self, text=None, frames=90):
        self.bubble = [text or self.pick_line("idle") or "honk.", frames]
        self.last_spoke = time.time()
        if self.args.sound:
            try:
                import winsound
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
            except Exception:
                pass

    def wait(self, n):
        for _ in range(n):
            yield

    def walk_to(self, tx, ty, speed=3.5, carry=None, stop=5):
        while True:
            dx, dy = tx - self.x, ty - self.y
            d = math.hypot(dx, dy)
            if d <= stop:
                return
            step = min(speed, d)
            self.x += dx / d * step
            self.y += dy / d * step
            if abs(dx) > 2:
                self.face = 1 if dx > 0 else -1
            self.phase += 0.35
            if carry:
                carry()
            yield

    def rand_spot(self, margin=120):
        return (random.randint(margin, SW_W - margin), random.randint(margin + 40, SW_H - margin))

    # -- behaviours (generators: one `yield` = one frame)
    def b_wander(self):
        for _ in range(random.randint(1, 3)):
            yield from self.walk_to(*self.rand_spot())
            if random.random() < 0.5:
                self.say()
            yield from self.wait(random.randint(20, 90))

    def b_scribble(self):
        yield from self.walk_to(*self.rand_spot(200))
        self.talk("scribble")
        color = random.choice(["#e63946", "#2a9d8f", "#6a4c93", "#f4a261", "#1d3557"])
        ang = random.uniform(0, 6.28)
        px, py = self.head()
        for _ in range(random.randint(90, 180)):
            ang += random.uniform(-0.7, 0.7)
            self.x = max(60, min(SW_W - 60, self.x + math.cos(ang) * 5))
            self.y = max(80, min(SW_H - 60, self.y + math.sin(ang) * 5))
            self.face = 1 if math.cos(ang) >= 0 else -1
            self.phase += 0.5
            nx, ny = self.head()
            item = self.c.create_line(px, py, nx, ny, width=5, fill=color,
                                      capstyle="round", tags="ink")
            self.ink.append((item, time.time()))
            px, py = nx, ny
            yield

    def b_minimize(self):
        if self.args.no_minimize or len(self.minimized) >= MAX_MINIMIZED:
            return
        wins = visible_windows()
        if not wins:
            return
        h, l, t, r, b = random.choice(wins)
        yield from self.walk_to(min(max((l + r) // 2, 60), SW_W - 60),
                                min(max(t + 40, 80), SW_H - 60))
        self.talk(f"minimize_{window_category(h)}", fallback="minimize")
        yield from self.wait(40)
        if user32.IsWindowVisible(h):
            user32.ShowWindow(h, 6)     # SW_MINIMIZE
            self.minimized.append([h, time.time() + random.randint(*MINIMIZE_RESTORE)])
            self.win("minimized")
        yield from self.walk_to(*self.rand_spot())

    def b_whiteboard(self):
        W, Hh = 340, 170
        side = random.choice([-1, 1])
        y = random.randint(220, SW_H - 260)
        edge_x = -90 if side == -1 else SW_W + 90
        yield from self.walk_to(edge_x, y, speed=5)
        wb = tk.Toplevel(self.root)
        wb.overrideredirect(True)
        wb.attributes("-topmost", True)
        wb.config(bg="white", highlightbackground="#444", highlightthickness=3)
        label = tk.Label(wb, text="", bg="white", fg="#1b1b1b", justify="left",
                         font=("Comic Sans MS", 14, "bold"))
        label.pack(expand=True, fill="both", padx=10, pady=10)

        def place():
            bx = self.x - self.face * (W / 2 + 40) - W / 2
            wb.geometry(f"{W}x{Hh}+{int(bx)}+{int(self.y - Hh / 2)}")
        try:
            self.face = -side           # walk back inwards
            place()
            yield from self.wait(60)
            yield from self.walk_to(SW_W / 2 + side * -200, y, speed=3, carry=place)
            self.win("boards")
            msg = self.pick_line("board") or random.choice(BOARD_TEXTS)
            for i in range(len(msg) + 1):
                label.config(text=msg[:i])
                yield from self.wait(4)
            yield from self.wait(150)
            self.talk("whiteboard_end")
            yield from self.walk_to(edge_x, y, speed=4, carry=place)
        finally:
            wb.destroy()

    def b_search(self):
        if self.args.no_edge:
            return
        before = set(edge_windows())
        try:
            subprocess.Popen(["cmd", "/c", "start", "", "msedge", "--new-window", "about:blank"],
                             creationflags=CREATE_NO_WINDOW)
        except Exception:
            return
        self.talk("search_start")
        hwnd = None
        for i in range(420):
            yield
            if i % 15 == 0:
                new = [h for h in edge_windows() if h not in before]
                if new:
                    hwnd = new[0]
                    break
        if hwnd is None:
            self.say("edge never came.", 60)
            return
        yield from self.wait(45)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        yield from self.walk_to(min(max(r.left + (r.right - r.left) * 0.4, 60), SW_W - 60),
                                min(max(r.top + 90, 80), SW_H - 60), speed=5)
        user32.SetForegroundWindow(hwnd)
        yield from self.wait(25)
        # Keystrokes only ever go to the Edge window the goose opened itself.
        if user32.GetForegroundWindow() != hwnd:
            self.say("it won't let me type.", 80)
            return
        send_ctrl_l()
        yield from self.wait(15)
        for ch in random.choice(SEARCHES_BY_STAGE[self.stage]):
            if user32.GetForegroundWindow() != hwnd:
                self.talk("search_interrupted")
                return
            send_char(ch)
            yield from self.wait(random.randint(3, 7))
        send_enter()
        self.win("searches")
        self.talk("search_done", frames=150)
        yield from self.wait(240)
        yield from self.walk_to(*self.rand_spot())

    # -- personality & context
    def pick_line(self, ctx, idx=None, **kw):
        table = DIALOGUE.get(ctx)
        if not table:
            return None
        lines = table[min(idx or self.stage, len(table)) - 1] or table[0]
        fresh = [l for l in lines if l not in self.recent]
        line = random.choice(fresh or lines)
        self.recent.append(line)
        fmt = {"cleaned": self.stats["cleaned"], "escapes": self.stats["escapes"],
               "tries": self.stats["cleaned"] + self.stats["escapes"] + self.stats["releases"],
               "mins": int((time.time() - self.start) / 60), "score": getattr(self, "score", 0), "grabs": getattr(self, "grabs", 0)}
        fmt.update(kw)
        try:
            return line.format(**fmt)
        except Exception:
            return line

    def talk(self, ctx, fallback=None, frames=None, idx=None, **kw):
        text = self.pick_line(ctx, idx=idx, **kw) or (self.pick_line(fallback, **kw) if fallback else None)
        if text:
            self.say(text, frames or min(70 + 4 * len(text), 260))

    def react(self, ctx, cooldown, force=False, **kw):
        """Comment on something the user is doing, with a per-topic cooldown."""
        now = time.time()
        if now - self.obs["said"].get(ctx, 0) < cooldown:
            return False
        if not force and (self.bubble is not None or now - self.last_spoke < SPEAK_GAP[self.stage - 1]):
            return False
        self.obs["said"][ctx] = now
        self.talk(ctx, **kw)
        return True

    def note_escape(self):
        self.stats["escapes"] += 1
        self.grudge += 1
        self.talk("escape")

    def note_release(self):
        self.stats["releases"] += 1
        self.grudge += 1
        self.talk("release")

    def update_stage(self):
        """Mood escalates with time and every time you fight back. It never calms down."""
        if self.args.stage:
            return
        heat = (time.time() - self.start) / 30 * HEAT_RATE + self.grudge * 3
        new = 1 + sum(heat >= t for t in (8, 20, 40))
        if new > self.stage:
            self.stage = new
            log(f"The goose is now at stage {new}")
            self.talk("stage_up", frames=240)

    def observe(self):
        now = time.time()
        o = self.obs
        cx, cy = get_cursor()
        lx, ly = o["cursor"]
        if not (self.cur is not None and self.cur_name in ("b_steal", "b_trash", "b_chase")):
            o["path"] += math.hypot(cx - lx, cy - ly)          # only YOUR mouse movement
        o["cursor"] = (cx, cy)
        near = math.hypot(cx - self.x, cy - self.y)
        down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
        if down and not o["down"]:
            o["clicks"].append(now)
            if near < 70 and not self.mode:
                self.react("click_near", 15, force=True)
        o["down"] = down
        # the goose won't be touched
        if ((self.cur is None or self.cur_name == "b_wander") and near < 90
                and not self.mode and now - o["dodge_t"] > 5):
            o["dodge_t"] = now
            if random.random() < min(0.8, 0.5 + 0.05 * self.grabs):   # it learns to dodge
                if self.cur is not None:
                    self.cur.close()
                self.cur_name = "b_dodge"
                self.cur = self.b_dodge()
        if self.mode:                                  # being dragged: no other commentary
            o["t"], o["path"] = now, 0.0
            return
        if now - o["t"] < 1.0:
            return
        dt, o["t"] = now - o["t"], now
        o["frantic"] = o["frantic"] + 1 if o["path"] / dt > 6000 else 0
        o["path"] = 0.0
        idle, was_idle = idle_seconds(), o["idle"]
        o["idle"] = idle
        h, cat = foreground_category()
        if h != o["fg"]:
            o["fg"], o["cat"], o["cat_t"] = h, cat, now
            if cat != "goose":
                o["switches"].append(now)
        while o["clicks"] and now - o["clicks"][0] > 3:
            o["clicks"].popleft()
        while o["switches"] and now - o["switches"][0] > 20:
            o["switches"].popleft()
        # pick at most one thing to say, most dramatic first
        if len(o["clicks"]) >= 7 and not self.poops and self.react("rage_click", 45):
            self.grudge += 0.5
        elif o["frantic"] >= 2 and self.react("mouse_frantic", 45):
            pass
        elif idle >= 60 > was_idle and self.react("idle_user", 90, force=True):
            pass
        elif idle < 5 <= was_idle and was_idle >= 60 and self.react("back", 30, force=True):
            pass
        elif len(o["switches"]) >= 6 and self.react("window_hopping", 90):
            pass
        elif (o["cat"] and o["cat"] not in ("goose", "other") and now - o["cat_t"] >= 8
              and self.react("app_" + o["cat"], 150)):
            pass
        else:
            if now - o["win_t"] > 20:
                o["win_t"] = now
                if len(visible_windows()) >= 12 and self.react("many_windows", 300):
                    return
            if self.grabs and self.react("grab_grudge", 100):
                return
            hour = time.localtime().tm_hour
            if hour >= 23 or hour < 5:
                self.react("late_night", 600)
            elif 12 <= hour < 14:
                self.react("lunch", 900)

    def b_dodge(self):
        cx, cy = get_cursor()
        dx, dy = self.x - cx, self.y - cy
        d = math.hypot(dx, dy)
        if d < 1:
            a = random.uniform(0, 6.283)
            dx, dy, d = math.cos(a), math.sin(a), 1
        dist = random.randint(260, 420)
        tx = min(max(self.x + dx / d * dist, 60), SW_W - 60)
        ty = min(max(self.y + dy / d * dist, 80), SW_H - 60)
        self.react("dodge", 10, force=True)
        yield from self.walk_to(tx, ty, speed=10 * (1 + 0.15 * (self.stage - 1)))

    def flair_anchor(self):
        hx, hy = self.head()
        f = self.face
        if self.sprite:
            return hx - f * 0.13 * self.sprite["w"], hy - 0.17 * self.sprite["h"]
        return hx - f * 12, hy - 11

    def draw_flair(self):
        """Stage 3+: angry brow. Stage 4: a crown. (Nudge FLAIR offsets here if your sprite differs.)"""
        if self.stage < 3:
            return
        c, f = self.c, self.face
        ax, ay = self.flair_anchor()
        c.create_line(ax - f * 12, ay + 5, ax + f * 7, ay + 12, width=4, fill="#222",
                      capstyle="round", tags="goose")
        if self.stage >= 4:
            c.create_polygon(ax - 12, ay, ax - 12, ay - 13, ax - 6, ay - 6, ax, ay - 15,
                             ax + 6, ay - 6, ax + 12, ay - 13, ax + 12, ay,
                             fill="#ffc400", outline="#b8860b", width=2, tags="goose")
            for jx in (-8, 0, 8):
                c.create_oval(ax + jx - 2, ay - 5, ax + jx + 2, ay - 1, fill="#d62828", outline="",
                              tags="goose")

    # -- scoreboard, achievements, confetti
    def win(self, key):
        """The goose scores a point (you never do)."""
        self.counts[key] = self.counts.get(key, 0) + 1
        self.score += 1
        if self.stage >= 3:
            self.confetti(*self.head())
        self.react("gloat", 30, score=self.score)

    def confetti(self, x, y):
        if self.args.no_hud or len(self.confetti_list) > 300:
            return
        for _ in range(45):
            a, sp = random.uniform(0, 6.283), random.uniform(3, 10)
            self.confetti_list.append([x, y, math.cos(a) * sp, math.sin(a) * sp - 4,
                                       random.choice(["#e63946", "#ffc400", "#2a9d8f", "#6a4c93", "#ff7f11"]),
                                       random.randint(70, 140)])

    def tick_extras(self):
        now = time.time()
        self.decoys = [d for d in self.decoys if d[3] > now]
        if not self.args.no_hud:
            for key, test, text in ACHIEVEMENTS:
                if key not in self.achieved and test(self):
                    self.achieved.add(key)
                    self.banners.append(text)
                    if self.stage >= 3:
                        self.confetti(SW_W / 2, 90)
            if self.banner is None and self.banners:
                self.banner = [self.banners.popleft(), 260]
            elif self.banner is not None:
                self.banner[1] -= 1
                if self.banner[1] <= 0:
                    self.banner = None
        for q in self.confetti_list:
            q[0] += q[2]
            q[1] += q[3]
            q[3] += 0.25
            q[5] -= 1
        self.confetti_list = [q for q in self.confetti_list if q[5] > 0 and q[1] < SW_H + 20]

    def draw_hud(self):
        c = self.c
        c.delete("hud")
        if self.args.no_hud:
            return
        t = c.create_text(22, SW_H - 82, text=f"GOOSE {self.score}   -   YOU 0", anchor="nw",
                          font=("Segoe UI", 11, "bold"), fill="white", tags="hud")
        bb = c.bbox(t)
        c.tag_lower(c.create_rectangle(bb[0] - 8, bb[1] - 4, bb[2] + 8, bb[3] + 4, fill="#222",
                                       outline="#ffc400", width=2, tags="hud"), t)
        if self.banner:
            bt = c.create_text(SW_W / 2, 96, text="ACHIEVEMENT UNLOCKED\n" + self.banner[0],
                               font=("Segoe UI", 14, "bold"), fill="#fff3c4", justify="center", tags="hud")
            bb = c.bbox(bt)
            c.tag_lower(c.create_rectangle(bb[0] - 18, bb[1] - 10, bb[2] + 18, bb[3] + 10, fill="#2b2d42",
                                           outline="#ffc400", width=3, tags="hud"), bt)
        for q in self.confetti_list:
            c.create_rectangle(q[0], q[1], q[0] + 6, q[1] + 9, fill=q[4], outline="", tags="hud")
        c.tag_raise("hud")

    def draw_decoys(self):
        if not self.decoys:
            return
        sx, sy, sf, sp = self.x, self.y, self.face, self.phase
        for d in self.decoys:
            d[4] += 0.25
            self.x, self.y, self.face, self.phase = d[0], d[1], d[2], d[4]
            if self.sprite:
                self.c.create_image(d[0], d[1] + math.sin(d[4] * 2) * 2, image=self.sprite[d[2]], tags="goose")
            else:
                self.draw_vector()
        self.x, self.y, self.face, self.phase = sx, sy, sf, sp

    # -- extra pranks
    def b_wobble(self):
        """Catch the cursor, then make it shaky for a few seconds. Any click ends it."""
        if self.args.no_mouse:
            return
        caught = yield from self.chase_cursor(CHASE_SPEED)
        if not caught:
            self.note_escape()
            if self.stage >= 2 and random.random() < 0.5:        # fake surrender...
                yield from self.wait(150)
                self.talk("psych")                               # ...and a sneak attack
                caught = yield from self.chase_cursor(CHASE_SPEED * 1.3)
            if not caught:
                return
        self.win("wobbles")
        self.talk("wobble_start")
        rx, ry = self.reach_xy()
        end = time.time() + random.uniform(6, 10)
        while time.time() < end:
            if self.args.click_release and mouse_clicked():
                self.note_release()
                return
            cx, cy = get_cursor()
            set_cursor(cx + random.randint(-3, 3), cy + random.randint(-3, 3))
            self.x += (cx - self.face * rx - self.x) * 0.25
            self.y += (cy + ry - self.y) * 0.25
            self.phase += 0.6
            yield
        self.talk("wobble_end")

    def b_stalk(self):
        """Tail the cursor, always a few steps behind, for ten seconds."""
        self.talk("stalk_start")
        end = time.time() + random.uniform(8, 12)
        speed = 7 * (1 + 0.12 * (self.stage - 1))
        while time.time() < end:
            cx, cy = get_cursor()
            side = 1 if cx < SW_W / 2 else -1
            gx, gy = cx + side * 70, cy + 60
            dx, dy = gx - self.x, gy - self.y
            d = math.hypot(dx, dy)
            if d > 3:
                step = min(speed, d)
                self.x += dx / d * step
                self.y += dy / d * step
                self.phase += 0.45
            self.face = -side
            yield
        self.talk("stalk_end")

    def b_graffiti(self):
        """Huge words across the screen (click-through; they fade like the scribbles)."""
        word = self.pick_line("graffiti") or "HONK"
        cx, cy = random.randint(300, SW_W - 300), random.randint(220, SW_H - 220)
        yield from self.walk_to(cx, cy + 90)
        ang = random.uniform(-14, 14)
        color = random.choice(["#e63946", "#6a4c93", "#ff7f11", "#1d3557"])
        for dx, dy, col in ((4, 4, "black"), (0, 0, color)):
            try:
                item = self.c.create_text(cx + dx, cy + dy, text=word, font=("Impact", 96), fill=col,
                                          angle=ang, tags="ink")
            except tk.TclError:
                item = self.c.create_text(cx + dx, cy + dy, text=word, font=("Impact", 96), fill=col, tags="ink")
            self.ink.append((item, time.time()))
        self.win("graffiti")
        yield from self.wait(90)

    def b_decoys(self):
        """Stage 3+: a crowd of fake geese pops up and the real one hides among them."""
        if self.stage < 3:
            return
        self.talk("decoys_start")
        for _ in range(random.randint(3, 5)):
            self.decoys.append([random.randint(80, SW_W - 80), random.randint(120, SW_H - 100),
                                random.choice([1, -1]), time.time() + random.uniform(6, 10),
                                random.uniform(0, 6)])
            yield from self.wait(12)
        d = random.choice(self.decoys)
        yield from self.walk_to(d[0] + 40, d[1], speed=6)
        self.talk("decoys_mid")
        yield from self.wait(240)

    # -- grabbing the goose
    def update_hit(self):
        if self.hit is None:
            return
        grabbable = self.can_grab()
        if grabbable != self.hit_grabbable:       # busy goose: clicks pass straight through it
            self.hit_grabbable = grabbable
            hw = user32.GetParent(self.hit.winfo_id()) or self.hit.winfo_id()
            ex = user32.GetWindowLongW(hw, -20)
            user32.SetWindowLongW(hw, -20, (ex & ~0x20) if grabbable else (ex | 0x20))
        if self.sprite:
            w, h = self.sprite["w"], self.sprite["h"]
            x0, y0 = self.x - w / 2, self.y - h / 2
        else:
            x0, y0, w, h = self.x - 48, self.y - 52, 96, 86
        geo = f"{int(w)}x{int(h)}{int(x0):+d}{int(y0):+d}"
        if geo != self.hit_geo:
            self.hit.geometry(geo)
            self.hit_geo = geo

    def can_grab(self):
        """Only when it's idle, just walking about, dodging, or already being thrown around."""
        if self.mode in ("drag", "toss"):
            return True
        return self.cur is None or self.cur_name in ("b_wander", "b_dodge")

    def on_press(self, e):
        if self.mode == "drag" or not self.can_grab():
            return
        self.grabs += 1
        self.grudge += 1.5                      # being handled makes it angrier overall
        if self.cur is not None:
            self.cur.close()                    # drops whatever it was doing (cursor, whiteboard...)
            self.cur = None
        self.mode = "drag"
        self.drag = {"dx": self.x - e.x_root, "dy": self.y - e.y_root, "mx": e.x_root, "my": e.y_root,
                     "t0": time.time(), "say_t": time.time(), "hist": collections.deque(maxlen=6)}
        show_cursor()
        self.talk("grab", idx=min(5, self.grabs), frames=110)
        log(f"Goose grabbed (#{self.grabs})")

    def on_motion(self, e):
        if self.mode == "drag" and self.drag:
            self.drag["mx"], self.drag["my"] = e.x_root, e.y_root
            self.drag["hist"].append((time.time(), e.x_root, e.y_root))

    def on_release(self, e):
        if self.mode != "drag" or not self.drag:
            return
        h = self.drag["hist"]
        vx = vy = 0.0
        if len(h) >= 2:
            (t0, x0, y0), (t1, x1, y1) = h[0], h[-1]
            dt = max(t1 - t0, 0.016)
            vx, vy = (x1 - x0) / dt * 0.016, (y1 - y0) / dt * 0.016    # px per frame
        self.vx, self.vy = max(-45, min(45, vx)), max(-45, min(45, vy))
        self.drag = None
        self.mode, self.toss_frames = "toss", 0
        self.talk("dropped", idx=min(5, self.grabs), frames=130)
        self.pending_revenge = random.random() < min(0.9, 0.25 * self.grabs)

    def step_mode(self):
        if self.mode == "drag":
            self.step_drag()
        elif self.mode == "toss":
            self.step_toss()

    def step_drag(self):
        d = self.drag
        if not d:
            self.mode = None
            return
        now = time.time()
        held = now - d["t0"]
        self.x += (d["mx"] + d["dx"] - self.x) * 0.45
        self.y += (d["my"] + d["dy"] - self.y) * 0.45
        self.phase += 1.1                                  # flailing
        self.face = 1 if int(held * 5) % 2 == 0 else -1    # glaring around
        self.grudge += 0.005                               # every second you hold it counts
        if now - d["say_t"] > 1.6:
            d["say_t"] = now
            self.talk("dragging", idx=min(5, self.grabs), frames=100)
        if held > max(2.5, 7 - 0.9 * self.grabs):          # it only puts up with it for so long
            self.mode, self.drag = None, None
            self.cur_name, self.cur = "b_breakfree", self.b_breakfree()

    def step_toss(self):
        self.toss_frames += 1
        self.vy += 0.9
        self.x += self.vx
        self.y += self.vy
        floor = SW_H - 120
        if self.x < 60:
            self.x, self.vx = 60, abs(self.vx) * 0.6
        if self.x > SW_W - 60:
            self.x, self.vx = SW_W - 60, -abs(self.vx) * 0.6
        if self.y < 80:
            self.y, self.vy = 80, abs(self.vy) * 0.5
        if self.y > floor:
            self.y, self.vy = floor, -abs(self.vy) * 0.5
            self.vx *= 0.85
        if abs(self.vx) > 1:
            self.face = 1 if self.vx > 0 else -1
        self.phase += 0.4
        if self.toss_frames > 300 or (self.y >= floor - 1 and abs(self.vx) < 0.8 and abs(self.vy) < 1.5):
            self.mode = None
            if self.pending_revenge:
                self.pending_revenge = False
                self.cur_name, self.cur = "b_revenge", self.b_revenge()
            else:
                self.idle = 90

    def b_breakfree(self):
        """It slips out of your grip, bolts, and (usually) comes back for payback."""
        self.talk("slip", idx=min(5, self.grabs), frames=110)
        tx, ty = self.rand_spot()
        yield from self.walk_to(tx, ty, speed=13)
        if random.random() < min(0.95, 0.4 + 0.15 * self.grabs):
            yield from self.b_revenge()

    def b_revenge(self):
        tier = min(5, self.grabs)
        self.talk("revenge", idx=tier, frames=120)
        yield from self.wait(45)
        if self.grabs >= 5 and not self.args.no_poop:       # full tantrum: poop bombs everywhere
            for _ in range(4):
                self.drop_poop(random.randint(80, SW_W - 80), random.randint(120, SW_H - 100))
                yield from self.wait(8)
            self.win("poops")
        mouse = [self.b_steal, self.b_trash, self.b_wobble, self.b_chase]
        other = [self.b_graffiti, self.b_poop, self.b_scribble]
        pool = other if self.args.no_mouse else mouse + other
        for _ in range(1 if self.grabs < 4 else (2 if self.grabs < 6 else 3)):
            pick = random.choice(pool)
            self.cur_name = pick.__name__
            yield from pick()

    # -- poop
    def tail_xy(self):
        if self.sprite:
            return (self.x - self.face * 0.38 * self.sprite["w"], self.y + 0.30 * self.sprite["h"])
        return self.x - self.face * 34, self.y + 26

    def b_poop(self):
        if self.args.no_poop:
            return
        for _ in range(random.randint(1, 3)):
            yield from self.walk_to(*self.rand_spot())
            self.talk("poop_drop")
            for i in range(28):                      # little squat-wiggle
                self.phase += 1.3
                yield
            self.drop_poop(*self.tail_xy())
            self.win("poops")
            yield from self.wait(25)

    def drop_poop(self, x, y, kind="pile", hp=None):
        if len(self.poops) >= MAX_POOPS:
            return None
        w, h = (84, 84) if kind == "pile" else (180, 120)
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.config(bg=KEY)
        win.attributes("-transparentcolor", KEY)     # only the brown pixels catch clicks
        c = tk.Canvas(win, width=w, height=h, bg=KEY, highlightthickness=0, cursor="hand2")
        c.pack()
        hp = hp or (POOP_HP if kind == "pile" else 2)
        pp = {"win": win, "c": c, "w": w, "h": h, "x": 0, "y": 0, "kind": kind, "hp": hp, "max": hp,
              "ang": random.uniform(-0.7, 0.7), "seed": random.randint(0, 10 ** 6)}
        c.bind("<Button-1>", lambda e, q=pp: self.scrub(q))
        self.poops.append(pp)
        self.place_poop(pp, x, y)
        self.paint_poop(pp)
        return pp

    def place_poop(self, pp, cx, cy):
        pp["x"] = int(min(max(cx - pp["w"] / 2, 0), SW_W - pp["w"]))
        pp["y"] = int(min(max(cy - pp["h"] / 2, 0), SW_H - pp["h"]))
        pp["c"].config(width=pp["w"], height=pp["h"])
        pp["win"].geometry(f"{pp['w']}x{pp['h']}+{pp['x']}+{pp['y']}")

    def remove_poop(self, pp):
        if pp in self.poops:
            self.poops.remove(pp)
        try:
            pp["win"].destroy()
        except Exception:
            pass

    def scrub(self, pp):
        if pp not in self.poops:
            return
        chance = SMEAR_CHANCE if pp["kind"] == "pile" else SMEAR_CHANCE / 2
        if random.random() < chance:
            self.smear(pp)
            return
        pp["hp"] -= 1
        if pp["hp"] <= 0:
            self.remove_poop(pp)
            self.stats["cleaned"] += 1
            self.grudge += 0.5
            if self.stats["cleaned"] % 5 == 0:
                self.talk("poop_milestone")
            elif random.random() < 0.4:
                self.talk("poop_clean")
        else:
            self.paint_poop(pp)

    def smear(self, pp):
        self.stats["smeared"] += 1
        self.talk("poop_smear")
        cx, cy = pp["x"] + pp["w"] / 2, pp["y"] + pp["h"] / 2
        if pp["kind"] == "pile":                    # a pile becomes a bigger smear
            pp.update(kind="smear", w=180, h=120, hp=3, max=3)
            pp["ang"] = random.uniform(-0.7, 0.7)
            self.place_poop(pp, cx, cy)
            spread = random.randint(1, 2)
        else:                                       # a smear only spreads, it doesn't grow
            spread = 1 if random.random() < 0.5 else 0
        pp["seed"] = random.randint(0, 10 ** 6)
        self.paint_poop(pp)
        for _ in range(spread):                     # smear it somewhere else on screen
            ang = random.uniform(0, 2 * math.pi)
            dist = random.randint(100, 240)
            self.drop_poop(cx + math.cos(ang) * dist, cy + math.sin(ang) * dist, "smear", hp=2)

    def paint_poop(self, pp):
        c, w, h = pp["c"], pp["w"], pp["h"]
        c.delete("all")
        sc = 0.5 + 0.5 * min(1.0, pp["hp"] / pp["max"])      # shrinks as you scrub it
        cx, cy = w / 2, h / 2
        if pp["kind"] == "pile":
            for dy, rx, ry in ((0.20, 0.40, 0.17), (0.00, 0.29, 0.15), (-0.19, 0.18, 0.13)):
                c.create_oval(cx - rx * w * sc, cy + dy * h * sc - ry * h * sc,
                              cx + rx * w * sc, cy + dy * h * sc + ry * h * sc,
                              fill=POOP, outline=POOP_D, width=2)
            c.create_polygon(cx - 7 * sc, cy - 0.30 * h * sc, cx + 2 * sc, cy - 0.45 * h * sc,
                             cx + 8 * sc, cy - 0.28 * h * sc, fill=POOP, outline=POOP_D, width=2)
            c.create_oval(cx - 0.22 * w * sc, cy - 0.04 * h * sc, cx - 0.08 * w * sc, cy + 0.04 * h * sc,
                          fill=POOP_L, outline="")
        else:
            rnd = random.Random(pp["seed"])
            for i in range(4):
                ang = pp["ang"] + rnd.uniform(-0.5, 0.5)
                ox, oy = rnd.uniform(-0.18, 0.18) * w, rnd.uniform(-0.15, 0.15) * h
                rx, ry = rnd.uniform(0.30, 0.46) * w * sc, rnd.uniform(0.08, 0.14) * h * sc
                c.create_polygon(ellipse_pts(cx + ox, cy + oy, rx, ry, ang),
                                 fill=POOP if i % 2 == 0 else "#6b4527",
                                 outline=POOP_D, width=1, smooth=True)
            for i in range(3):
                sx, sy = cx + rnd.uniform(-0.45, 0.45) * w, cy + rnd.uniform(-0.4, 0.4) * h
                r = rnd.uniform(3, 7) * sc
                c.create_oval(sx - r, sy - r, sx + r, sy + r, fill=POOP, outline="")

    def chase_cursor(self, speed, give_up=None):
        """Run after the LIVE cursor until the beak reaches it. Returns True if caught."""
        speed *= 1 + 0.12 * (self.stage - 1)           # a more annoyed goose runs faster
        end = time.time() + (give_up or CHASE_GIVEUP)
        rx, ry = self.reach_xy()
        while time.time() < end:
            cx, cy = get_cursor()
            if abs(cx - self.x) > rx + 10:          # only turn round once clearly past
                self.face = 1 if cx > self.x else -1
            gx, gy = cx - self.face * rx, cy + ry
            dx, dy = gx - self.x, gy - self.y
            d = math.hypot(dx, dy)
            if d < 10:
                return True
            step = min(speed, d)
            self.x += dx / d * step
            self.y += dy / d * step
            self.phase += 0.45
            yield
        return False

    def b_shove(self):
        """Carry a window across the screen; it is put back a little later."""
        if self.args.no_minimize or len(self.shoved) >= 2:
            return
        wins = [w for w in visible_windows() if not user32.IsZoomed(w[0])]
        if not wins:
            return
        h, l, t, r, b = random.choice(wins)
        yield from self.walk_to(min(max(l + 60, 60), SW_W - 60), min(max(t + 30, 80), SW_H - 60))
        self.talk("shove_grab")
        offx, offy = l - self.x, t - self.y
        self.shoved.append([h, l, t, time.time() + random.randint(20, 35)])
        gx = random.randint(60, SW_W - 60)
        gy = random.randint(80, SW_H - 60)

        def carry():
            user32.SetWindowPos(h, 0, int(self.x + offx), int(self.y + offy), 0, 0, 0x15)
        yield from self.walk_to(gx, gy, speed=4, carry=carry)
        self.win("shoves")
        self.talk("shove")

    def b_chase(self):
        """Run at the cursor and fling it somewhere else. Repeat."""
        if self.args.no_mouse:
            return
        self.talk("chase_start")
        for _ in range(random.randint(3, 6)):
            caught = yield from self.chase_cursor(CHASE_SPEED)
            if not caught:
                self.note_escape()
                break
            sx, sy = get_cursor()
            nx, ny = self.rand_spot(60)
            for i in range(1, 13):                 # a quick throw, not an instant jump
                k = i / 12
                set_cursor(sx + (nx - sx) * k, sy + (ny - sy) * k)
                yield
            self.talk("fling")
            yield from self.wait(random.randint(10, 30))

    def b_steal(self):
        if self.args.no_mouse:
            return
        self.talk("steal_start")
        caught = yield from self.chase_cursor(CHASE_SPEED)
        if not caught:
            self.note_escape()
            return
        self.win("steals")
        end = time.time() + random.uniform(*STEAL_SECS)
        hide_cursor()
        try:
            def carry():
                set_cursor(*self.head())
            released = False
            while time.time() < end and not released:
                yield from self.walk_to(*self.rand_spot(), speed=DRAG_SPEED, carry=carry)
                if self.args.click_release and mouse_clicked():
                    released = True
            self.note_release() if released else self.talk("steal_end")
        finally:
            show_cursor()

    def b_trash(self):
        if self.args.no_mouse:
            return
        real = self.bin_pos if (self.bin_pos and desktop_visible_at(*self.bin_pos)) else None
        tx, ty = real if real else (SW_W - 110, SW_H - 170)
        if not real:
            self._draw_trash(tx, ty)       # fallback: draw a fake bin
        st = {"free": False, "clicked": False}

        def release():
            if not st["free"]:
                st["free"] = True
                show_cursor()

        def click_release():                      # you clicked to break free
            if not st["free"]:
                st["clicked"] = True
                self.stats["releases"] += 1
                self.grudge += 1
            release()

        def carry():
            if self.args.click_release and mouse_clicked():
                click_release()
            elif not st["free"]:
                set_cursor(*self.head())

        def pin():
            if self.args.click_release and mouse_clicked():
                click_release()
            elif not st["free"]:
                set_cursor(tx, ty)

        try:
            self.talk("trash_start")
            rx, ry = self.reach_xy()
            caught = yield from self.chase_cursor(CHASE_SPEED)
            if not caught:
                self.note_escape()
                if self.stage >= 2 and random.random() < 0.5:
                    yield from self.wait(150)
                    self.talk("psych")
                    caught = yield from self.chase_cursor(CHASE_SPEED * 1.3)
                if not caught:
                    return
            hide_cursor()
            fd = 1 if tx > SW_W / 2 else -1       # approach the bin from the screen-centre side
            stage_x = min(max(tx - fd * (rx + 140), 60), SW_W - 60)
            yield from self.walk_to(stage_x, ty + ry, speed=DRAG_SPEED, carry=carry)
            yield from self.walk_to(tx - fd * rx, ty + ry, speed=DRAG_SPEED * 0.7, carry=carry)
            self.face = fd
            pin()
            self.win("trashed")
            self.talk("trash_delete", frames=220)
            end = time.time() + TRASH_SECS
            # step away so the goose isn't standing on the bin; mouse stays pinned inside it
            away_x = min(max(tx - fd * (rx + 300), 60), SW_W - 60)
            yield from self.walk_to(away_x, ty + ry - 60, speed=DRAG_SPEED, carry=pin)
            while time.time() < end and not st["free"]:
                pin()
                yield
            release()
            self.talk("release" if st["clicked"] else "trash_back")
            yield from self.walk_to(*self.rand_spot())
        finally:
            show_cursor()
            self.c.delete("trash")

    def _draw_trash(self, x, y):
        c = self.c
        c.create_polygon(x - 34, y - 20, x + 34, y - 20, x + 28, y + 40, x - 28, y + 40,
                         fill="#8d99ae", outline="#2b2d42", width=3, tags="trash")
        c.create_rectangle(x - 40, y - 32, x + 40, y - 20, fill="#adb5bd",
                           outline="#2b2d42", width=3, tags="trash")
        for dx in (-12, 0, 12):
            c.create_line(x + dx, y - 12, x + dx * 0.8, y + 32, fill="#2b2d42", width=3, tags="trash")
        c.create_text(x, y + 58, text="Trash", fill="white", font=("Segoe UI", 10, "bold"), tags="trash")

    # -- rendering
    def draw(self):
        self.c.delete("goose")
        self.c.delete("bubble")
        if self.sprite:
            bob = math.sin(self.phase * 2) * 2
            self.c.create_image(self.x, self.y + bob, image=self.sprite[self.face], tags="goose")
        else:
            self.draw_vector()
        self.draw_decoys()
        self.draw_flair()
        self.draw_bubble()
        self.draw_hud()
        self.update_hit()

    def draw_vector(self):
        c, f = self.c, self.face
        x, y = self.x, self.y
        sw = math.sin(self.phase) * 5
        for off, s in ((-8, sw), (8, -sw)):
            c.create_line(x + off, y + 14, x + off + s, y + 30, width=4, fill="#f4a100", tags="goose")
            c.create_oval(x + off + s - 7, y + 28, x + off + s + 9 * f, y + 34, fill="#f4a100",
                          outline="", tags="goose")
        c.create_polygon(x - f * 20, y - 2, x - f * 40, y - 14, x - f * 26, y + 8,
                         fill="white", outline="#9a9a9a", tags="goose")
        c.create_oval(x - 26, y - 16, x + 26, y + 18, fill="white", outline="#9a9a9a", width=2, tags="goose")
        c.create_oval(x - 14, y - 10, x + 12, y + 8, fill="#ececec", outline="#bdbdbd", tags="goose")
        hx, hy = self.head()
        nx, ny = x + f * 16, y - 6
        c.create_line(nx, ny, hx, hy, width=12, fill="#9a9a9a", capstyle="round", tags="goose")
        c.create_line(nx, ny, hx, hy, width=9, fill="white", capstyle="round", tags="goose")
        c.create_oval(hx - 10, hy - 8, hx + 10, hy + 8, fill="white", outline="#9a9a9a", width=2, tags="goose")
        c.create_polygon(hx + f * 8, hy - 3, hx + f * 22, hy + 1, hx + f * 8, hy + 5,
                         fill="#ff8c00", outline="#c76a00", tags="goose")
        c.create_oval(hx + f * 2 - 2, hy - 4, hx + f * 2 + 2, hy, fill="black", tags="goose")

    def draw_bubble(self):
        c = self.c
        hx, hy = self.head()
        if self.bubble:
            text, left = self.bubble
            fill, outline, size = BUBBLE_STYLE[self.stage - 1]
            jx = random.randint(-2, 2) if self.stage == 4 else 0      # the unhinged shake
            jy = random.randint(-2, 2) if self.stage == 4 else 0
            t = c.create_text(hx + jx, hy - 34 + jy, text=text, font=("Segoe UI", size, "bold"),
                              fill="black", width=320, justify="center", anchor="s", tags="bubble")
            bb = c.bbox(t)
            r = c.create_rectangle(bb[0] - 7, bb[1] - 4, bb[2] + 7, bb[3] + 4, fill=fill,
                                   outline=outline, width=3 if self.stage >= 3 else 2, tags="bubble")
            c.tag_lower(r, t)
            self.bubble[1] -= 1
            if self.bubble[1] <= 0:
                self.bubble = None
        c.tag_raise("trash")
        c.tag_raise("goose")
        c.tag_raise("bubble")

    # -- housekeeping / loop
    def restore_shoved(self, force=False):
        now = time.time()
        for entry in list(self.shoved):
            h, ox, oy, due = entry
            if force or now > due:
                if user32.IsWindow(h) and not user32.IsZoomed(h) and not user32.IsIconic(h):
                    user32.SetWindowPos(h, 0, int(ox), int(oy), 0, 0, 0x15)  # NOSIZE|NOZORDER|NOACTIVATE
                self.shoved.remove(entry)

    def housekeeping(self):
        now = time.time()
        for item in list(self.ink):
            if now - item[1] > INK_LIFETIME:
                self.c.delete(item[0])
                self.ink.remove(item)
        for entry in list(self.minimized):
            if now > entry[1]:
                if user32.IsIconic(entry[0]):
                    user32.ShowWindow(entry[0], 9)   # SW_RESTORE
                self.minimized.remove(entry)
        self.restore_shoved()

    def pick_behaviour(self):
        if time.time() - self.start < GRACE_SECS:
            self.cur_name = "b_wander"
            return self.b_wander()
        names = [self.b_wander, self.b_scribble, self.b_minimize,
                 self.b_whiteboard, self.b_steal, self.b_trash, self.b_search,
                 self.b_shove, self.b_chase, self.b_poop, self.b_wobble, self.b_stalk,
                 self.b_graffiti, self.b_decoys]
        weights = WEIGHTS
        fn = random.choices(names, weights)[0]
        self.cur_name = fn.__name__
        return fn()

    def tick(self):
        if not self.running:
            return
        try:
            self._tick()
        except Exception:
            self.errors += 1
            log(f"ERROR in '{self.cur_name}' (#{self.errors}):\n{traceback.format_exc()}")
            self.recover()
            if self.errors >= 15:
                self.quit("too many errors, see goose_log.txt")
        if self.running:
            self.root.after(FPS_MS, self.tick)

    def recover(self):
        """Put things back after an error and carry on instead of freezing."""
        try:
            if self.cur is not None:
                self.cur.close()
        except Exception:
            pass
        self.cur = None
        self.idle = 120
        self.bubble = None
        show_cursor()

    def _tick(self):
        if panic_pressed():
            self.quit("panic key pressed")
            return
        if self.deadline:
            left = self.deadline - time.time()
            if left <= 0:
                self.quit(f"time limit reached ({self.args.minutes:g} min; use --minutes 0 for no limit)")
                return
            if left < 4 and not self.warned:
                self.warned = True
                self.say("shift's over. honk.", 200)
        self.housekeeping()
        self.update_stage()
        self.tick_extras()
        if not self.args.no_context:
            self.observe()
        if self.mode:
            self.step_mode()
            self.draw()
            return
        if self.cur is None:
            if self.idle > 0:
                self.idle -= 1
            else:
                self.cur = self.pick_behaviour()
        if self.cur is not None:
            try:
                next(self.cur)
            except StopIteration:
                self.cur = None
                self.idle = int(random.randint(*IDLE_FRAMES) * (1 - 0.12 * (self.stage - 1)))
                self.obs["dodge_t"] = time.time()
        self.draw()

    def quit(self, reason="quit"):
        log(f"Goose quitting: {reason}")
        self.running = False
        if self.cur is not None:
            self.cur.close()           # runs finally-blocks (cursor, whiteboard)
        show_cursor()
        for h, _ in self.minimized:
            if user32.IsIconic(h):
                user32.ShowWindow(h, 9)
        self.minimized.clear()
        self.restore_shoved(force=True)
        for pp in list(self.poops):
            try:
                pp['win'].destroy()
            except Exception:
                pass
        self.poops.clear()
        self.root.destroy()

    def run(self):
        self.root.after(FPS_MS, self.tick)
        try:
            self.root.mainloop()
        finally:
            show_cursor()


def main():
    p = argparse.ArgumentParser(description="Harmless desktop goose prank.")
    p.add_argument("--no-mouse", action="store_true")
    p.add_argument("--no-minimize", action="store_true")
    p.add_argument("--no-edge", action="store_true")
    p.add_argument("--no-poop", action="store_true")
    p.add_argument("--no-context", action="store_true")
    p.add_argument("--no-hud", action="store_true")
    p.add_argument("--no-drag", action="store_true")
    p.add_argument("--click-release", action="store_true")
    p.add_argument("--stage", type=int, choices=[0, 1, 2, 3, 4], default=0)
    p.add_argument("--chaos", type=int, choices=[1, 2, 3], default=2,
                   help="1 gentle, 2 default, 3 full goose")
    p.add_argument("--minutes", type=float, default=30)
    p.add_argument("--sound", action="store_true")
    p.add_argument("--sprite", help="path to a PNG of your goose (faces right)")
    p.add_argument("--sprite-shrink", type=int, default=2,
                   help="shrink the PNG by this whole-number factor (default 2)")
    args = p.parse_args()
    apply_chaos(args.chaos)
    try:
        faulthandler.enable(open(LOG_PATH, "a"))     # records hard crashes too
    except Exception:
        pass
    log("Goose released. Panic keys: Ctrl+Shift+Q or Ctrl+Alt+Q"
        + (f" | auto-quit in {args.minutes:g} min" if args.minutes > 0 else " | no time limit"))
    try:
        Goose(args).run()
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        show_cursor()
        try:
            input("The goose crashed. Details are in goose_log.txt. Press Enter to close...")
        except Exception:
            pass


if __name__ == "__main__":
    main()