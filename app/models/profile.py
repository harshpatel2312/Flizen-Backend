from typing import Any, Optional
from pydantic import BaseModel, field_validator
import re


class PersonalInfo(BaseModel):
    firstName:   Optional[str] = None
    lastName:    Optional[str] = None
    dob:         Optional[str] = None
    gender:      Optional[str] = None
    nationality: Optional[str] = None


class ContactInfo(BaseModel):
    email:   Optional[str] = None
    phone:   Optional[str] = None
    country: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v.strip() == "":
            return v
        if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$", v.strip()):
            raise ValueError("Invalid email address")
        return v.strip()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v.strip() == "":
            return v
        if not re.match(r"^\+?[\d\s\-(). ]{7,20}$", v.strip()):
            raise ValueError("Invalid phone number")
        return v.strip()


class TravelPreferences(BaseModel):
    cabinClass:  Optional[str] = None
    currency:    Optional[str] = None
    homeAirport: Optional[Any] = None   # airport object from AirportAutocomplete
    seatPref:    Optional[str] = None
    mealPref:    Optional[str] = None


class PassportInfo(BaseModel):
    number:  Optional[str] = None
    country: Optional[str] = None
    expiry:  Optional[str] = None

    @field_validator("number")
    @classmethod
    def validate_number(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v.strip() == "":
            return v
        if not re.match(r"^[A-Z0-9]{6,9}$", v.strip()):
            raise ValueError("Passport number must be 6–9 uppercase letters/digits")
        return v.strip()

    @field_validator("expiry")
    @classmethod
    def validate_expiry(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v.strip() == "":
            return v
        if not re.match(r"^(0[1-9]|1[0-2])\/\d{4}$", v.strip()):
            raise ValueError("Expiry must be MM/YYYY")
        return v.strip()


class ProfileData(BaseModel):
    personal:    Optional[PersonalInfo]      = None
    contact:     Optional[ContactInfo]       = None
    preferences: Optional[TravelPreferences] = None
    passport:    Optional[PassportInfo]      = None
