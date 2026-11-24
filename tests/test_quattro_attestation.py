import base64
import json

import pytest

from scripts.omarchy_quattro_build import verify_attestation_status


REF = 'ghcr.io/joshyorko/omarchy-bootc@sha256:' + 'a' * 64
SOURCE = 'b' * 40


def envelope(source=SOURCE, digest='sha256:' + 'a' * 64):
    statement = {'predicate': {
        'schema': 'omarchy-bootc.published-image/v1',
        'image': REF.split('@')[0], 'source_sha': source,
        'oci_manifest_digest': digest, 'acceptance_overlay': 'not-applied',
    }}
    return {'payload': base64.b64encode(json.dumps(statement).encode()).decode()}


def test_cosign_envelope_stream_matches_exact_source_not_only_digest():
    wrong = envelope(source='c' * 40)
    accepted = envelope()
    stream = json.dumps(wrong) + '\n' + json.dumps(accepted) + '\n'
    assert verify_attestation_status(0, stream, REF, REF.split('@')[1], SOURCE) == [wrong, accepted]


@pytest.mark.parametrize('detail', [
    json.dumps(envelope(source='c' * 40)),
    json.dumps(envelope(digest='sha256:' + 'd' * 64)),
    json.dumps(envelope()) + '\nnot-json', '[]', 'null',
])
def test_missing_mismatching_or_malformed_verified_claim_is_rejected(detail):
    with pytest.raises(ValueError):
        verify_attestation_status(0, detail, REF, REF.split('@')[1], SOURCE)


def test_failed_cosign_cannot_be_overridden_by_matching_output():
    with pytest.raises(ValueError):
        verify_attestation_status(1, json.dumps(envelope()), REF, REF.split('@')[1], SOURCE)


def test_array_output_remains_compatible():
    accepted = envelope()
    assert verify_attestation_status(0, json.dumps([accepted]), REF, REF.split('@')[1], SOURCE) == [accepted]
