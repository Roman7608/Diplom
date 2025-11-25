import re
from typing import Optional


def normalize_phone(phone: str) -> Optional[str]:
    """
    Normalize phone number to +7XXXXXXXXXX format.
    Returns None if phone is invalid.
    """
    # Remove all non-digit characters
    digits = re.sub(r'\D', '', phone)
    
    # Handle different formats
    if digits.startswith('8') and len(digits) == 11:
        # Russian format starting with 8
        digits = '7' + digits[1:]
    elif digits.startswith('7') and len(digits) == 11:
        # Already in +7 format
        pass
    elif len(digits) == 10:
        # Missing country code, assume Russia
        digits = '7' + digits
    else:
        return None
    
    # Validate length (should be 11 digits: 7 + 10)
    # Original code required exactly 11.
    # User requirement: "check not less than 10 digits".
    # Actually, normalized phone +7XXXXXXXXXX is always 12 chars (+ and 11 digits).
    # The logic above already ensures 'digits' has length 11 (for RU).
    # If it's foreign? 
    # Let's stick to RU format mostly, but if we want "at least 10", 
    # it means we accept 10 digits (without country code maybe?) 
    # The code above handles 10 digits by prepending 7.
    
    if len(digits) < 11: # Should contain at least 11 digits (including country code)
        return None
        
    # If more than 15, probably trash
    if len(digits) > 15:
        return None
    
    # Standardize to +7 for RU/KZ
    if digits.startswith('7') and len(digits) == 11:
        return '+' + digits
        
    # For others, just +digits
    return '+' + digits


def validate_phone(phone: str) -> bool:
    """
    Check if phone number is valid.
    Returns True if phone can be normalized, False otherwise.
    """
    return normalize_phone(phone) is not None

