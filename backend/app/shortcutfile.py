"""Build the iPhone shortcut for the customer, so they do not have to.

WHY
The manual route is five actions typed into the Shortcuts app, and the reply to
it was "or else create shortcut automatically and on click everything should be
done" -- which is fair. A backup that takes five minutes of following
instructions is one people abandon halfway and then believe they have.

A .shortcut file is a plist. iOS will import one straight from a URL via the
`shortcuts://import-shortcut?url=` scheme, so the whole thing can be one tap.

WHAT THIS IS NOT
Signed. Apple signs shortcuts shared through iCloud links, and signing needs a
Mac and an Apple ID; neither belongs in a customer's copy of this app. An
unsigned import may therefore need Settings -> Shortcuts -> Allow Untrusted
Shortcuts, and on some iOS versions may be refused outright. That is why the
manual steps stay on the screen underneath rather than being replaced: this is
offered as the quick way, not the only way.

The action identifiers and parameter shapes below are the documented ones, but
they are Apple's private format and this file cannot be tested from the machine
that builds it. If an import fails or imports something that does not run, the
manual steps are the fallback, and the shape to check first is WFFormValues --
attaching the repeat item as a FILE is the fiddly part.
"""
from __future__ import annotations

import plistlib
import uuid


def _text(s: str) -> dict:
    """A plain string in the place Shortcuts expects a token string."""
    return {"Value": {"string": s}, "WFSerializationType": "WFTextTokenString"}


def _variable(name: str) -> dict:
    """A magic variable — 'Repeat Item' is the photo currently being sent."""
    return {"Value": {"Type": "Variable", "VariableName": name},
            "WFSerializationType": "WFTextTokenAttachment"}


def _dict_field(items: list[dict]) -> dict:
    return {"Value": {"WFDictionaryFieldValueItems": items},
            "WFSerializationType": "WFDictionaryFieldValue"}


def build(url: str, token: str, app_name: str = "SafeNest") -> bytes:
    """Find Photos -> Repeat with Each -> POST each one to `url`."""
    group = str(uuid.uuid4()).upper()

    find_photos = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.filter.photos",
        # No filter and no limit: the whole library is the point. A limit here is
        # what turns "back up everything" back into "back up a selection".
        "WFWorkflowActionParameters": {"UUID": str(uuid.uuid4()).upper()},
    }
    repeat_start = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group,
            "WFControlFlowMode": 0,          # 0 = the opening half
        },
    }
    post = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "WFURL": url,
            "WFHTTPMethod": "POST",
            "WFHTTPBodyType": "Form",
            "WFHTTPHeaders": _dict_field([{
                "WFItemType": 0,
                "WFKey": _text("Authorization"),
                # The space after Bearer is not decoration; without it the header
                # is a different scheme and every upload is refused.
                "WFValue": _text(f"Bearer {token}"),
            }]),
            "WFFormValues": _dict_field([{
                "WFItemType": 5,             # 5 = file, not text
                "WFKey": _text("file"),
                "WFValue": _variable("Repeat Item"),
            }]),
        },
    }
    repeat_end = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group,
            "WFControlFlowMode": 2,          # 2 = the closing half
        },
    }

    workflow = {
        "WFWorkflowClientVersion": "1146.7",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 946986751,
            "WFWorkflowIconGlyphNumber": 59511,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": ["NCWidget"],
        "WFWorkflowInputContentItemClasses": [
            "WFAppStoreAppContentItem", "WFArticleContentItem",
            "WFContactContentItem", "WFDateContentItem",
            "WFEmailAddressContentItem", "WFGenericFileContentItem",
            "WFImageContentItem", "WFiTunesProductContentItem",
            "WFLocationContentItem", "WFDCMapsLinkContentItem",
            "WFAVAssetContentItem", "WFPDFContentItem",
            "WFPhoneNumberContentItem", "WFRichTextContentItem",
            "WFSafariWebPageContentItem", "WFStringContentItem",
            "WFURLContentItem",
        ],
        "WFWorkflowActions": [find_photos, repeat_start, post, repeat_end],
        "WFQuickActionSurfaces": [],
    }
    return plistlib.dumps(workflow, fmt=plistlib.FMT_BINARY)
