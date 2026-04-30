from datetime import datetime, timedelta, timezone

def calculate_sm2(quality: int, repetitions: int, easiness_factor: float, interval: int) -> tuple[int, float, int]:
    """
    SuperMemo-2 (SM-2) algorithm implementation.
    
    :param quality: Grade representing how well the user remembered (0-5 scale)
                    0: Blank/no answer
                    1: Wrong
                    2: Incorrect but seems familiar
                    3: Correct but with difficulty
                    4: Correct
                    5: Perfect response
    :param repetitions: Count of successful reviews in a row
    :param easiness_factor: Real number >= 1.3
    :param interval: Current interval in days
    :return: (new_repetitions, new_easiness_factor, new_interval)
    """
    
    if quality >= 3:
        if repetitions == 0:
            interval = 1
        elif repetitions == 1:
            interval = 6
        else:
            interval = round(interval * easiness_factor)
        repetitions += 1
    else:
        repetitions = 0
        interval = 1
        
    easiness_factor = easiness_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    if easiness_factor < 1.3:
        easiness_factor = 1.3
        
    return repetitions, easiness_factor, interval

def map_score_to_quality(score: int, total: int) -> int:
    """
    Maps a quiz score (e.g. 4/5 correct) to SM-2 quality (0-5).
    """
    if total == 0:
        return 0
    percentage = score / total
    
    if percentage >= 1.0:
        return 5
    elif percentage >= 0.8:
        return 4
    elif percentage >= 0.6:
        return 3
    elif percentage >= 0.4:
        return 2
    elif percentage >= 0.2:
        return 1
    else:
        return 0
