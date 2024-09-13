# -*- coding: utf-8 -*-
import logging
import os

import requests

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOGLEVEL", "INFO").upper())

BASEURL = os.environ.get("BASEURL")

"""
Sample trigger: PreSignUp_SignUp
{
'version': '1',
'region': 'us-west-2',
'userPoolId': 'us-west-2_ftAhAA70g',
'userName': 'mihai+bobo3@re-collect.ai',
'callerContext': {'awsSdkVersion': 'aws-sdk-unknown-unknown', 'clientId': '37sl5upjevqmrhkhnmr96j8v19'},
'triggerSource': 'PreSignUp_SignUp',
'request': {'userAttributes': {'custom:invitation': '078f302c-79f8-4367-9770-0a1d48215b8d', 'email': 'mihai+bobo3@re-collect.ai'}, 'validationData': None}, 'response': {'autoConfirmUser': False, 'autoVerifyEmail': False, 'autoVerifyPhone': False}
}

Sample trigger: PostConfirmation_ConfirmSignUp
{
'version': '1',
'region': 'us-west-2',
'userPoolId': 'us-west-2_ftAhAA70g',
'userName': 'mihai+bobo3@re-collect.ai',
'callerContext': {'awsSdkVersion': 'aws-sdk-unknown-unknown', 'clientId': '37sl5upjevqmrhkhnmr96j8v19'},
'triggerSource': 'PostConfirmation_ConfirmSignUp',
'request': {'userAttributes': {'sub': 'da1739ce-79d4-4797-8237-f04d20f47194', 'email_verified': 'true', 'cognito:user_status': 'CONFIRMED', 'custom:invitation': '078f302c-79f8-4367-9770-0a1d48215b8d',
'email': 'mihai+bobo3@re-collect.ai'}},
'response': {}
}

"""


def enforce_invitation(event, context):
    email = event["request"]["userAttributes"]["email"]

    # Note: leave this false
    event["response"]["autoConfirmUser"] = False

    invitation = event["request"]["userAttributes"].get("custom:invitation")
    if not invitation:
        logger.info(f"Failing: no invitation provided for email {email}")
        raise Exception("missing invitation")

    # Attempt to validate invitation
    payload = {
        "email": email,
        "invitation_code": invitation,
    }
    response = requests.post(f"{BASEURL}/validate-invite", json=payload)

    match response.status_code:
        # see src/http_servers/api_routers/signup_webhook.py
        case 200:
            return event
        case 403:
            logger.warning(f"Invitation not found for invite {invitation}")
            raise Exception("invitation invalid")
        case 409:
            logger.warning(f"Account already exists {email}")
            raise Exception("account already exists")
        case 410:
            logger.warning(f"Failing: invitation already used {invitation}")
            raise Exception("invitation already used")
        case _:
            message = f"ERROR: unexpected HTTP {response.status_code}"
            logger.error(message)
            raise Exception(message)


def complete_invitation(event, context):
    email = event["request"]["userAttributes"]["email"]
    name = event["request"]["userAttributes"].get("custom:name")
    invitation = event["request"]["userAttributes"].get("custom:invitation")
    # user_id = auth.cognito_id = Cognito userName = Cognito User ID (Sub)
    user_id = event["request"]["userAttributes"]["sub"]

    if not invitation:
        # assume this is a post-verification call, just return event
        return event

    # Mark invitation as used
    payload = {
        "name": name,
        "email": email,
        "invitation_code": invitation,
        "user_id": user_id,
    }
    response = requests.post(f"{BASEURL}/create-account", json=payload)

    if response.status_code != 201:
        message = f"ERROR: unexpected HTTP {response.status_code}"
        logger.error(message)
        raise Exception(message)

    return event


_trigger_handler_map = {
    "PreSignUp_SignUp": enforce_invitation,
    "PostConfirmation_ConfirmSignUp": complete_invitation,
}


def lambda_handler(event, context):
    trigger = event.get("triggerSource")

    logger.info(f"Got event {event}")
    logger.warning(f"Invoked with trigger {trigger}")

    if not trigger:
        logger.warning(f"No trigger in event: {event}")
        return event
    if trigger in _trigger_handler_map:
        return _trigger_handler_map[trigger](event, context)

    logger.warning(f"Unrecognized trigger: {trigger} event: {event}")

    return event
