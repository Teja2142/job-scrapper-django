from __future__ import annotations

from dataclasses import dataclass

from ..core.jscompat import js_string, js_trim, jsre, truthy

STATE_MAP = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR',
    'california': 'CA', 'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE',
    'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID',
    'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS',
    'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV',
    'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM', 'new york': 'NY',
    'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
    'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT',
    'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV',
    'wisconsin': 'WI', 'wyoming': 'WY',
    'district of columbia': 'DC',
}

STATE_CODES = set(STATE_MAP.values())

# "CA" -> "California"
STATE_NAMES = {
    code: ' '.join(w if w == 'of' else w[0].upper() + w[1:] for w in name.split(' '))
    for name, code in STATE_MAP.items()
}

_DOT = jsre(r'\.')
_DIGITS = jsre(r'[0-9]')


def state_name_from_code(code) -> str | None:
    """"CA" -> "California". Unknown / missing code -> None."""
    if not truthy(code):
        return None
    return STATE_NAMES.get(js_string(code).upper())


def state_code_from_text(text) -> str | None:
    """"Illinois" | "IL" | "Il." -> "IL". Anything that is not a US state (or DC) -> None."""
    if not truthy(text):
        return None
    t = _DOT.sub('', js_trim(js_string(text)))
    upper = t.upper()
    if upper in STATE_CODES:
        return upper
    return STATE_MAP.get(t.lower())


@dataclass
class ParsedLocation:
    city: str | None = None
    state: str | None = None
    stateCode: str | None = None
    isRemote: bool = False
    isHybrid: bool = False


def parse_location(raw) -> ParsedLocation:
    result = ParsedLocation()

    if not truthy(raw):
        return result
    if not isinstance(raw, str):
        raise TypeError('raw.toLowerCase is not a function')

    lower = raw.lower()
    if 'remote' in lower:
        result.isRemote = True
    if 'hybrid' in lower:
        result.isHybrid = True

    # "San Francisco, CA" | "New York, New York" | "Austin, TX 78701"
    parts = [js_trim(p) for p in raw.split(',')]

    if len(parts) >= 2:
        result.city = parts[0] or None
        state_chunk = _DOT.sub('', parts[1].split(' ')[0]).upper()  # strip zip, "D.C." -> "DC"
        if state_chunk in STATE_CODES:
            result.stateCode = state_chunk
            result.state = STATE_NAMES[state_chunk]
        else:
            full = js_trim(_DIGITS.sub('', parts[1].lower()))
            if full in STATE_MAP:
                result.stateCode = STATE_MAP[full]
                result.state = STATE_NAMES[result.stateCode]  # canonical name, no stray zip digits

    return result
