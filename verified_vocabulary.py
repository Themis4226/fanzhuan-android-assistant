"""Local vocabulary derived only from accepted answers, never model proposals.

Read task logs on this computer. No account, device, coordinates, credentials or
task-completion state is carried into another session. Nothing is uploaded.
"""
import json
from pathlib import Path
import re


def records(path):
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            item = json.loads(line)
        except (ValueError, TypeError):
            continue  # Incomplete last lines must never become a dictionary entry.
        if isinstance(item, dict):
            yield item


def chinese(text):
    return ''.join(re.findall(r'[\u4e00-\u9fff]', text))


def accepted_definitions(root):
    for folder in sorted((Path(root)/'runs').glob('*')):
        for item in records(folder/'correct_word_answers.jsonl'):
            if item.get('feedback') == 'green correct answer row observed':
                yield item.get('word'), item.get('definition')
        # daily_spell emits this event only after the app accepts a submission
        # (counter advances or the final task-complete page appears).
        for item in records(folder/'events.jsonl'):
            if (item.get('event') != 'daily_spelling_answer'
                    or item.get('source') not in {'reviewed_OCR_answer', 'verified_local_answer'}
                    or item.get('api_calls') != 0):
                continue
            match = re.fullmatch(r'(\d+)/(\d+)', str(item.get('progress', '')))
            if match and 1 <= int(match[1]) <= int(match[2]) <= 30:
                yield item.get('word'), item.get('clue')


def checked_definitions(root):
    for word, meanings in accepted_definitions(root):
        if (not isinstance(word, str) or not re.fullmatch(r'[A-Z]?[a-z]{1,29}', word)
                or not isinstance(meanings, list)
                or not meanings or any(not isinstance(x, str) for x in meanings)):
            continue
        clean = [text for text in meanings if chinese(text)]
        if clean:
            yield word, clean


def load_definitions(root):
    bank = {}
    for word, clean in checked_definitions(root):
        for text in clean:
            values = bank.setdefault(word, [])
            if text not in values:
                values.append(text)
    return bank


def spelling_bank(root):
    bank = {}
    for word, meanings in checked_definitions(root):
        # Some actual clues wrap into several OCR regions. Keep individual
        # meanings and the complete joined clue, always using exact Chinese.
        for meaning in meanings + [''.join(meanings)]:
            key = chinese(meaning)
            if key:
                bank.setdefault(key, set()).add(word)
    return bank
