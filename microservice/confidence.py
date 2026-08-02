HEAVY_FLAGS={"missing_date", "ambiguous_date", "conflicting_signals"}
LIGHT_FLAGS = {"missing_sender_org", "vague_subject"}

HEAVY_PENALTY=0.25
LIGHT_PENALTY=0.10

def compute_confidence(flags:list,attachment_unparsed:bool=False)->float:
    score=1.0

    for flag in flags:
        if flag in HEAVY_FLAGS:
            score-=HEAVY_PENALTY
        elif flag in LIGHT_FLAGS:
            score-=LIGHT_PENALTY

    if attachment_unparsed:
        score-=HEAVY_PENALTY

    score=max(0,score)
    return round(score,2)