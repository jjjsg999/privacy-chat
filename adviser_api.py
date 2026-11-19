"""Persona library backend; conversation UI is added in the next stage."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import persona_store

router = APIRouter(prefix='/api/personas')


class PersonaWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=500)
    filename: str = Field(max_length=100)
    text: str = Field(min_length=1, max_length=20_000)


def operation(function, **kwargs):
    try:
        return function(**kwargs)
    except persona_store.PersonaError as exc:
        raise HTTPException(400, str(exc)) from None
    except OSError:
        raise HTTPException(503, 'Persona storage unavailable.') from None


@router.get('')
async def personas():
    data = operation(persona_store.load)
    return {'revision': data['revision'], 'personas': [p for p in data['personas'].values() if not p['archived']]}


@router.post('')
async def import_persona(value: PersonaWrite):
    return operation(persona_store.save, **value.model_dump())


@router.put('/{identifier}')
async def edit_persona(identifier: str, value: PersonaWrite):
    return operation(persona_store.save, identifier=identifier, **value.model_dump())


@router.delete('/{identifier}')
async def archive_persona(identifier: str, expected_revision: int):
    return operation(persona_store.archive, identifier=identifier, expected_revision=expected_revision)
