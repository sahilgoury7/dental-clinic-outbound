import asyncio
import os
import argparse
from dotenv import load_dotenv

from livekit import api

load_dotenv()

async def create_dispatch_rule(trunk_id: str, rule_name: str, room_prefix: str = "inbound-"):
    """
    Create a SIP dispatch rule that routes calls from a specific trunk to dynamically created rooms.
    """
    # Using the standard LiveKit Server API
    livekit_api = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET")
    )

    try:
        # Create a dispatch rule that routes calls to a dynamic room based on prefix + caller number
        # For example, room name: inbound-+1234567890
        rule = api.sip.CreateSIPDispatchRuleRequest(
            name=rule_name,
            rule=api.sip.SIPDispatchRule(
                dispatch_rule_individual=api.sip.SIPDispatchRuleIndividual(
                    room_prefix=room_prefix
                )
            ),
            trunk_ids=[trunk_id] if trunk_id else []
        )
        
        result = await livekit_api.sip.create_sip_dispatch_rule(rule)
        print(f"✅ Successfully created SIP Dispatch Rule!")
        print(f"Rule ID: {result.sip_dispatch_rule_id}")
        print(f"Name: {result.name}")
        print(f"Trunk IDs: {result.trunk_ids}")
        print(f"This rule will route incoming calls to rooms with prefix '{room_prefix}'")
        print("\nNote: Make sure your LiveKit agent is listening for rooms matching this prefix or running as a global worker.")
        
    except Exception as e:
        print(f"❌ Failed to create dispatch rule: {e}")
    finally:
        await livekit_api.aclose()

async def list_dispatch_rules():
    livekit_api = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET")
    )
    try:
        rules = await livekit_api.sip.list_sip_dispatch_rule()
        print("\n--- Current SIP Dispatch Rules ---")
        for r in rules.items:
            print(f"ID: {r.sip_dispatch_rule_id} | Name: {r.name}")
        print("----------------------------------")
    except Exception as e:
        print(f"Failed to list rules: {e}")
    finally:
        await livekit_api.aclose()

async def delete_dispatch_rule(rule_id: str):
    livekit_api = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET")
    )
    try:
        await livekit_api.sip.delete_sip_dispatch_rule(api.sip.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule_id))
        print(f"✅ Deleted rule {rule_id}")
    except Exception as e:
        print(f"Failed to delete rule {rule_id}: {e}")
    finally:
        await livekit_api.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage LiveKit SIP Dispatch Rules")
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")

    create_parser = subparsers.add_parser("create", help="Create a new dispatch rule")
    create_parser.add_argument("--name", type=str, default="dental-inbound-rule", help="Name of the rule")
    create_parser.add_argument("--trunk-id", type=str, help="Specific SIP Trunk ID to associate this rule with (optional)")
    create_parser.add_argument("--prefix", type=str, default="inbound-", help="Room name prefix for incoming calls")

    list_parser = subparsers.add_parser("list", help="List all dispatch rules")
    
    delete_parser = subparsers.add_parser("delete", help="Delete a dispatch rule")
    delete_parser.add_argument("rule_id", type=str, help="ID of the rule to delete")

    args = parser.parse_args()

    if args.action == "create":
        asyncio.run(create_dispatch_rule(args.trunk_id, args.name, args.prefix))
    elif args.action == "list":
        asyncio.run(list_dispatch_rules())
    elif args.action == "delete":
        asyncio.run(delete_dispatch_rule(args.rule_id))
    else:
        parser.print_help()
