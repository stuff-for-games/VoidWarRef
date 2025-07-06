import os
import re
import json
import pprint
from datetime import date
from pathlib import Path
import base64

################################################################################
#
# 	Overview: 
# 	----------------------------------------------------------------------------
# 	DataDump.csx run in UntertaleModTool
# 		dumps /data.json & /code/*.gml
# 	
# 	Load data.json: data.json -> exp_data
# 		Contains categories, objects and their hierarchy, object tags
# 	Load additional game code
# 		Extract data that isn't a direct part of the displayed objects
# 	Parse object code: exp_data & .gml files -> parsed_code
#		Load data based on exp_data
# 		Perform very basic k=v and function call extraction
# 		Patch edge cases that aren't supported by resolve_ & proc_ functions
# 	Process object code: parsed_code -> proc_data
# 		Use proc_ & resolve_ functions on each object in each category
# 		Prepares data sp it's suitable for display
#	Process static data
#		Mostly static data not worth extracting by automatic means, i.e. Shrines
# 	Render HTML: proc_data & ref_template.html -> HTML
#
# 	Warning:
# 	----------------------------------------------------------------------------
#	In the context of this script parsing means: crude, line by line, 
#	context unaware, substring & regex juggling madness. Strap in.
#	In general: expect some jank and non-pythonic python
#
# 	Sections:
# 	----------------------------------------------------------------------------
# 	Config & Storage
# 	Processing
# 	Processing Utils
# 	Parsing & Patching
# 	General Utils
# 	Rendering Output
# 	Main run()
# 
################################################################################

################################################################################
## MARK: Config & Storage
################################################################################

base_dir = os.getcwd()
export_dir = Path(os.path.join(base_dir, "gml_code"))
data_json_path = Path(os.path.join(base_dir, "gml_code", "data.json"))
template_path = Path(os.path.join(base_dir, "ref_template.html"))

debug_config = {
	"include_context": [],
	"last_context": None
}

col_title_abbreviations = { 
	# effect chances
	"Fire": "&#x1F525;&#xfe0e;",
	"Breach": "&#x1F4A5;&#xfe0e;",
	"Warp Breach": "Warp &#x1F4A5;&#xfe0e;",
	# resistances
	"Fire Resistance": "&#x1F525;&#xfe0e;",
	"Poison Resistance": "&#x2623;",
	"Vacuum Resistance": "O<sub>2</sub>",
	# crew abilities
	"Repair System": "&#x1f527;&#xfe0e;",
	"Extinguish Fire": "&#x1F525;&#xfe0e;",
	"Attack System": "&#x2694;&#xfe0e;",
	"Man System": "&#x1F6B6;&#xfe0e;",
	# systems
	"Mannable": "&#x1F6B6;&#xfe0e;",
	"Manning Bonus": "&#x1F6B6;&#xfe0e;&nbsp;&nbsp;Bonus",
	"Max Energy": "Max &#x1F5F2;", 
	"Upgrade Cost": 'Upg. <div class="scrap_icon"><div class="scrap_1">&#x025A0;</div><div class="scrap_2">&#x025A1;</div></div>',
	# common
	"Energy": "&#x1F5F2;", 
	"Charge Time": "&#x023F1;",
	"Buy Price": '<div class="scrap_icon"><div class="scrap_1">&#x025A0;</div><div class="scrap_2">&#x025A1;</div></div>'
}

# ship weapons and modules are filtered through their "buyable<type>" tag
unavailable_obj_list = [
	"oSysPoisonProjector", "oSysViralBombard", "oSysBossBarrow",
	"oSysDemonHeart.*", "oSysVault.*",
	"oCrewPlayerTranshuman",
	"oConsumableTeleportRoomRandom", "oConsumableTeleportSelfRandom",
	"oItemSummonZombieBloodInstant", "oItemTeleportSelfRandom",
	"oKWFirefighter",
	"oEFLabel", "oEFAddAbility", 
]

cat_config = {
	"Systems": { "fn": "proc_system" },
	"Subsystems": { "fn": "proc_system" },
	"Modules": { "fn": "proc_module" },
	"Armaments": { 
		"fn": "proc_ship_weapon", 
		"group": { "key": "Type", "order": ["Standard", "Area", "Lance", "Beam"] },
		"col_span": { "Projectiles": "proj", "Beam": "beam", "Damage": "dmg", "Effect Chance": "efc" } 
	},
	"Missiles": { "fn": "proc_missile", "col_span": { "Damage": "dmg", "Effect Chance": "efc" } },
	"Commanders": { 
		"fn": "proc_commander",
		"col_span": { "Resistances": "res", "Abilities": "abl" }
	},
	"Crew": { 
		"fn": "proc_crew", 
		"col_span": { "Resistances": "res", "Abilities": "abl" }
	},
	"Consumables": { "fn": "proc_consumable" },
	"Armor": { "fn": "proc_armor" },
	"Psychomancies": { "fn": "proc_psychomancy" },
	"Tools": { "fn": "proc_tool" },
	"Weapons": { "fn": "proc_weapon" },
	"Keywords": { "fn": "proc_keyword" },
	"Effects": { "fn": "proc_effect" },
	"Shrines": { "fn": None },
}

# storage:
exp_data = {} # data.json
parsed_code = {} # { object_name : simple kv represantion of vars/calls in object code }
proc_data = {} # { object_name : processed data for display }

# additional game data
global_labels = {}
global_vars = {}
spawn_crew_for_script = {}
extra_effect_durations = {}

################################################################################
## MARK: Processing
################################################################################

def proc_object_code():
	for cat in exp_data["objCatData"]:
		name = cat["name"]
	
		config = cat_config.get(name)
		if config == None:
			continue
		proc_data[name] = []

		for obj_name in cat["objNames"]:
			proc_fn_name = config["fn"]
			obj_list = hierarchy_for_object(obj_name)

			entry = globals()[proc_fn_name](obj_list)
			entry["InternalName"] = obj_name[1:]
			entry["ObjTags"] = ", ".join(exp_data["objTagsMap"][obj_name])
			
			for filter in unavailable_obj_list:
				if re.match(filter + "$", obj_name):
					entry["__unavailable"] = True
					break

			proc_data[name].append(entry)
	
		proc_data[name].sort(key=lambda e: e["Name"])

def proc_static():
	l = [{
		"Name": global_labels["label_factionShrine_blood"],
		"InternalName": "ShrineNodeBlood",
		"Faction": resolve_str("name", ["oKWFaction_blood"]),
		"Extra Effects": "Commander loses 50% of current HP and cannot rest for 3 jumps, ship fight reward",
		"Challenge": "Win ship fight"		
	}, {
		"Name": global_labels["label_factionShrine_death"],
		"InternalName": "ShrineNodeDeath",
		"Faction": resolve_str("name", ["oKWFaction_death"]),
		"Extra Effects": "Commander cannot rest for 3 jumps",
		"Challenge": f"Win ship fight; Kill enemy boarders with broken sensors and 50% move speed debuff for 30s. 8x {obj_link("oCrewZombie_plain")}, " + 
						f"Sector >=3: 10x random({obj_link("oCrewZombie_plain")}, {obj_link("oCrewZombie_pox")}), " + 
						f"Sector >=4: +2  random({obj_link("oCrewZombie_lord")}, {obj_link("oCrewZombie_psy")})."
	}, {
		"Name": global_labels["label_factionShrine_war"],
		"InternalName": "ShrineNodeWar",
		"Faction": resolve_str("name", ["oKWFaction_war"]),
		"Extra Effects": "Commander cannot rest for 3 jumps, 3 ship fight rewards",
		"Challenge": "Win 3 ship fights"		
	}, {
		"Name": global_labels["label_factionShrine_empire"],
		"InternalName": "ShrineNodeEmpire",
		"Faction": resolve_str("name", ["oKWFaction_empire"]),
		"Extra Effects": "Full crew heal, +10 Hull restored",
		"Challenge": "None"		
	}, {
		"Name": global_labels["label_factionShrine_raider"],
		"InternalName": "ShrineNodeRaider",
		"Faction": resolve_str("name", ["oKWFaction_raider"]),
		"Extra Effects": "Small reward (i.e. 50% low scrap; 5% ship weapon or module; or low tier item)",
		"Challenge": "None"		
	}, {
		"Name": global_labels["label_factionShrine_techno"],
		"InternalName": "ShrineNodeTechno",
		"Faction": resolve_str("name", ["oKWFaction_techno"]),
		"Extra Effects": "Small reward (i.e. 50% low scrap; 5% ship weapon or module; or low tier item)",
		"Challenge": "Answer 3 questions like a machine would"		
	}]

	proc_data["Shrines"] = l
	for entry in proc_data["Shrines"]:
		entry["ObjTags"] = ""

def proc_system(obj_list):
	entry = {}
	entry["Name"] = resolve_str("name", obj_list)
	entry["Buy Price"] = resolve_num("buyPrice", obj_list)
	entry["Max Energy"] = resolve_num("maxUpgradedHP", obj_list)
	mannable = resolve_bool("mannable", obj_list)
	entry["Manning Bonus"] = resolve_str("manningBonusDescription", obj_list) if mannable else ""

	proc_system_upgrades(obj_list, entry)

	desc = resolve_str("description", obj_list)
	if obj_list[0] == "oSysBloodPit":
		desc += f" ({resolve_num("spawnTime", obj_list)}s build time)"
	elif obj_list[0] == "oSysSonicAmplifier":
		desc += f" ({resolve_num("applyKWLifespan", obj_list)}s duration)"
	entry["Description"] = desc

	return entry

def proc_module(obj_list):
	entry = {}
	entry["Name"] = resolve_str("name", obj_list)
	entry["Buy Price"] = resolve_num("buyPrice", obj_list)
	entry["Description"] = resolve_str("description", obj_list)

	if "buyableModule" not in exp_data["objTagsMap"][obj_list[0]]:
		entry["__unavailable"] = True

	return entry

def proc_commander(obj_list):	
	entry = {}
	entry["Name"] = resolve_str("baseName", obj_list)
	entry["HP"] = resolve_num("baseMaxHP", obj_list)
	entry["DPS"] = resolve_num("baseDPS", obj_list)
	entry["Speed"] = proc_crew_movespeed(obj_list)
	entry["Faction"] = proc_crew_faction(obj_list)
	entry["Slots"] = proc_crew_slots(obj_list)
	proc_crew_resistances(obj_list, entry)
	entry["Equipment"] = proc_crew_items(obj_list)
	entry["Keywords"] = proc_crew_keywords(obj_list)
	entry["Description"] = resolve_str("commanderDescription", obj_list)
	return entry

def proc_crew(obj_list):
	entry = {}
	entry["Name"] = resolve_str("baseName", obj_list)
	entry["Buy Price"] = resolve_num("buyPrice", obj_list)
	entry["HP"] = resolve_num("baseMaxHP", obj_list)
	entry["DPS"] = resolve_num("baseDPS", obj_list)
	entry["Speed"] = proc_crew_movespeed(obj_list)
	entry["Faction"] = proc_crew_faction(obj_list)
	entry["Slots"] = proc_crew_slots(obj_list)
	proc_crew_resistances(obj_list, entry)
	entry["abl:Man System"] = resolve_bool("canManSystem", obj_list)
	entry["abl:Repair System"] = resolve_bool("canRepair", obj_list)
	entry["abl:Extinguish Fire"] = resolve_bool("canExtinguish", obj_list)
	entry["abl:Attack System"] = resolve_bool("cannotAttackSystems", obj_list)
	entry["Equipment"] = proc_crew_items(obj_list)
	entry["Keywords"] = proc_crew_keywords(obj_list)
	return entry
	
def proc_ship_weapon(obj_list):	
	entry = {}
	
	type = "Standard"
	is_area = resolve_bool("isAreaWeapon", obj_list)
	if is_area:
		type = "Area"
	is_lance = resolve_bool("isLance", obj_list)
	if is_lance:
		type = "Lance"
	is_beam = resolve_bool("isBeam", obj_list)
	if is_beam:
		type = "Beam"
	
	entry["Name"] = resolve_str("name", obj_list)
	entry["Buy Price"] = resolve_num("buyPrice", obj_list)
	entry["Type"] = type
	entry["Energy"] = resolve_num("psiCost", obj_list)
	entry["Charge Time"] = resolve_num("chargeTime", obj_list)
	entry["Pierce"] = resolve_num("shieldPiercing", obj_list)
	
	if is_area:
		entry["proj:Count"] = resolve_num("areaWeapon_projectileCt", obj_list)
		entry["proj:Spread"] = resolve_num("areaWeapon_targetRadius", obj_list)
	elif is_lance:
		entry["Max Charges"] = resolve_num("maxLanceCharges", obj_list)
		entry["proj:Count"] = resolve_num("numberOfShots", obj_list)
	elif is_beam:
		entry["beam:Length"] = resolve_num("beamLength", obj_list)
		entry["beam:Speed"] = resolve_num("beamSpeed", obj_list)
	else:
		entry["proj:Count"] = resolve_num("numberOfShots", obj_list)
	if not is_beam:
		entry["proj:Speed"] = proc_projectile_speed(obj_list)

	entry["dmg:Hull"] = resolve_num("shotDamage", obj_list)
	entry["dmg:Ion"] = resolve_num("ionDamage", obj_list)
	entry["dmg:Crew"] = resolve_num("crewDamage", obj_list)

	entry["efc:Fire"] = resolve_num("fireChance", obj_list)
	entry["efc:Breach"] = resolve_num("breachChance", obj_list)
	entry["efc:Warp Breach"] = resolve_num("warpBreachChance", obj_list)
	entry["Effects"] = proc_weapon_keywords(obj_list, not is_lance)

	if "buyableWeapon" not in exp_data["objTagsMap"][obj_list[0]]:
		entry["__unavailable"] = True
	
	return entry

def proc_missile(obj_list):	
	entry = {}
	entry["Name"] = resolve_str("name", obj_list)
	entry["Buy Price"] = resolve_num("buyPrice", obj_list)
	entry["Charge Time"] = resolve_num("chargeTime", obj_list)
	entry["Ammo Cost"] = resolve_num("missileAmmoCostPerShot", obj_list)
	entry["Projectile Speed"] = proc_projectile_speed(obj_list)

	entry["dmg:Hull"] = resolve_num("shotDamage", obj_list)
	entry["dmg:Ion"] = resolve_num("ionDamage", obj_list)
	entry["dmg:Crew"] = resolve_num("crewDamage", obj_list)

	entry["efc:Fire"] = resolve_num("fireChance", obj_list)
	entry["efc:Breach"] = resolve_num("breachChance", obj_list)
	entry["efc:Warp"] = resolve_num("warpBreachChance", obj_list)	

	entry["Description"] = proc_weapon_keywords(obj_list, False)
	return entry

def proc_consumable(obj_list):	
	entry = {}
	entry["Name"] = resolve_str("name", obj_list)
	entry["Buy Price"] = resolve_num("buyPrice", obj_list)
	entry["Description"] = resolve_str("description", obj_list)
	proc_extra_item_ability_attributes(obj_list, entry)
	proc_item_summon(obj_list, entry)
	return entry

def proc_armor(obj_list):	
	entry = {}
	entry["Name"] = resolve_str("name", obj_list)
	entry["Buy Price"] = resolve_num("buyPrice", obj_list)
	entry["Description"] = resolve_str("description", obj_list)
	return entry

def proc_psychomancy(obj_list):	
	entry = {}
	entry["Name"] = resolve_str("name", obj_list)
	entry["Buy Price"] = resolve_num("buyPrice", obj_list)
	entry["Description"] = resolve_str("description", obj_list)
	proc_extra_item_ability_attributes(obj_list, entry)
	proc_item_summon(obj_list, entry)
	return entry

def proc_tool(obj_list):
	entry = {}
	entry["Name"] = resolve_str("name", obj_list)
	entry["Buy Price"] = resolve_num("buyPrice", obj_list)
	entry["Description"] = resolve_str("description", obj_list)
	proc_extra_item_ability_attributes(obj_list, entry)	
	return entry

def proc_weapon(obj_list):	
	entry = {}
	entry["Name"] = resolve_str("name", obj_list)
	entry["Buy Price"] = resolve_num("buyPrice", obj_list)
	entry["Description"] = resolve_str("description", obj_list)
	return entry

def proc_keyword(obj_list):	
	entry = {}
	name = resolve_str("name", obj_list)
	desc = ""

	if name:
		entry["Name"] = name
	else:
		entry["Name"] = obj_list[0][len("oKW"):]
	
	desc = resolve_str("description", obj_list) or ""
		
	for args in args_for_calls(obj_list[0], "effect_add"):
		if args[0] == "oEFLabel":
			continue
		if len(desc) > 0:
			desc += "; "
		val = args[1]
				
		# val is object name
		if re.match(r'^o\w+$', val) != None:
			val = obj_link(val, val[1:])
		# non-numeric val
		elif re.match(r'^-?\d+(?:\.\d+)?$', val) == None:
			val = resolve_str(val, obj_list)

		val = str(proc_resistance_value(args[0], val))

		if val.startswith("choose("):
			val = proc_random_item(obj_list, val)

		desc += obj_link(args[0], args[0][1:]) + "=" + str(val)
	
	# usually only spells or tools reference abilities, link to corresponding item if there is one
	abilityName = resolve_raw("addsAbility", obj_list) or ""
	abilityName = abilityName.replace("oAbl", "oItem")
	if abilityName in parsed_code:
		desc += obj_link(abilityName, resolve_str("name", [abilityName]))
	
	entry["Description"] = desc
	return entry

def proc_effect(obj_list):
	entry = {}
	entry["Name"] = obj_list[0][len("oEF"):]
	entry["Stacks"] = proc_effect_stacking(obj_list)
	entry["Description"] = resolve_str("description", obj_list)
	return entry

################################################################################
## MARK: Processing Utils
################################################################################

def proc_system_upgrades(obj_list, entry):
	max_energy = entry["Max Energy"]
	upgrade_costs = []
	upgrade_tiers = []
	cost_start_i = 1
	if obj_list[0] == "oSysShields":
		cost_start_i = 2
	for i in range(cost_start_i, max_energy):
		cost = str(resolve_num("upgradeLevelCost", obj_list, i))
		upgrade_costs.append(cost)
	for i in range(0, max_energy):
		tier = resolve_str("upgradeTierDescription", obj_list, i)
		if tier:
			upgrade_tiers.append(tier)
	
	if len(upgrade_costs) > 3:
		upgrade_costs = ["/".join(upgrade_costs[:3]) + "/", "/".join(upgrade_costs[3:])]
		entry["Upgrade Cost"] = upgrade_costs
	else:
		entry["Upgrade Cost"] = "/".join(upgrade_costs)
	if obj_list[0] == "oSysEngines":
		entry["Tiers"] = upgrade_tiers[0] + " per level"
	else:
		entry["Tiers"] = list(dict.fromkeys(upgrade_tiers))

def proc_effect_stacking(obj_list):
	stacks = resolve_bool("enableStacking", obj_list)
	if stacks:
		stack_type = resolve_raw("stackingType", obj_list)
		if stack_type == "UnknownEnum.Value_0":
			return "Add"
		elif stack_type == "UnknownEnum.Value_5":
			return "Mult"
	return ""

def proc_projectile_speed(obj_list):
	ret_val = None
	
	entry = parsed_code[obj_list[0]]

	speed = resolve_num("projectileSpeed", obj_list)
	if speed:
		return speed

	for args in args_for_calls(obj_list[0], "weapon_set_projectile"):
		proj_name = args[0]
		speed = resolve_num("projectileSpeed", hierarchy_for_object(proj_name))
		if speed:
			return speed
	
	return ret_val

def proc_crew_slots(obj_list):
	slots = []
	for i in range(4):
		slot = resolve_str("slotType", obj_list, i)
		hide = resolve_bool("hideSlot", obj_list, i)
		if hide != True and slot != "none":
			slots.append(slot.capitalize()[0])
	
	return " ".join(slots)

def proc_crew_items(obj_list):
	all_names = []
	enemy_item_names = {}
	player_item_names = {}
	display_names = []

	for args in args_for_calls(obj_list[0], "ds_list_set"):
		if args[0] != "setShopEntryItem" and args[0] != "setItem":
			continue

		index = int(args[1])
		name = args[2]

		innate_prefix = ""
		if resolve_bool("hideSlot", obj_list, index):
			innate_prefix = "Innate: "
		
		if args[0] == "setShopEntryItem":
			player_item_names[index] = innate_prefix + proc_random_item(obj_list, name)
			all_names.append(player_item_names[index])
		elif args[0] == "setItem":
			enemy_item_names[index] = innate_prefix + proc_random_item(obj_list, name)
			all_names.append(enemy_item_names[index])

	is_player = exp_data["objParentMap"][obj_list[0]] == "oCrewPlayer"
	all_names = list(dict.fromkeys(all_names))
	for name in all_names:
		prefix = ""
		in_enemy = name in enemy_item_names.values()
		in_player = name in player_item_names.values()
		if is_player or in_enemy and in_player:
			prefix = ""
		elif in_player:
			prefix = "P: "
		elif in_enemy:
			prefix = "E: "
		
		display_names.append(prefix + name)

	return "; ".join(display_names)

def proc_crew_faction(obj_list):
	for args in args_for_calls(obj_list[0], "crew_init_keywords"):
		for kw in args:
			if kw.startswith("oKWFaction"):
				return resolve_str("name", [kw])
	return ""

def proc_crew_movespeed(obj_list):
	for kw_args in args_for_calls(obj_list[0], "crew_init_keywords"):
		if "oKWSessile" in kw_args or "oKWImmobilized" in kw_args:
			return 0
	for args in args_for_calls(obj_list[0], "crew_init_base_movespeed"):
		return float(args[0])
	return resolve_num("baseMoveSpeed", obj_list)

def proc_crew_keywords(obj_list):
	kws = []
	kw_calls = args_for_calls(obj_list[0], "crew_init_keywords")
	args = kw_calls[0] if len(kw_calls) > 0 else []

	if obj_list[1] != "oCrew":
		parent_kw_calls = args_for_calls(obj_list[1], "crew_init_keywords")
		if len(parent_kw_calls) > 0:
			args.extend(parent_kw_calls[0])
			args = list(dict.fromkeys(args))

	for kw in args:
		if kw.startswith("oKWFaction"):
			continue
		kw_desc = resolve_str("name", [kw])

		if kw_desc == None or len(kw_desc) < 1:
			kw_desc = obj_link(kw, kw[len("oKW"):])
		else:
			kw_desc = obj_link(kw, kw_desc)

		kws.append(kw_desc)
	return "; ".join(kws)

def proc_crew_resistances(obj_list, entry):
	kws = []
	kw_calls = args_for_calls(obj_list[0], "crew_init_keywords")
	args = kw_calls[0] if len(kw_calls) > 0 else []

	if obj_list[1] != "oCrew":
		parent_kw_calls = args_for_calls(obj_list[1], "crew_init_keywords")
		if len(parent_kw_calls) > 0:
			args.extend(parent_kw_calls[0])
			args = list(dict.fromkeys(args))
	
	entry[f"res:Fire Resistance"] = 0
	entry[f"res:Poison Resistance"] = 0
	entry[f"res:Vacuum Resistance"] = 0
	for kw in args:
		res_effect = None
		for args in args_for_calls(kw, "effect_add"):
			res_match = re.match(r'oEF(\w+)Resistance', args[0])
			if res_match:
				res_val = args[1]
				if re.match(r'-?\d+', res_val) == None:
					res_val = resolve_num(res_val, [kw])
				entry[f"res:{res_match.group(1)} Resistance"] = proc_resistance_value(args[0], res_val)

def proc_weapon_keywords(obj_list, include_ign_shields):
	descs = []

	# replicates generate_weapon_description() for attributes that don't get
	# their own column
	if resolve_bool("hullDamageOnly", obj_list):
		descs.append(global_labels["label_doesNotDamageSystems"])
	noSysDmgMulti = resolve_num("hullDamageMultiplierForSystemless", obj_list)
	if noSysDmgMulti and noSysDmgMulti > 1:
		descs.append(f"{noSysDmgMulti}x {global_labels["label_damageToSystemlessRooms"]}")
	ignoreShields = resolve_bool("ignoreShields", obj_list)
	if include_ign_shields and ignoreShields == True:
		descs.append(global_labels["label_ignoresShield"])
	dmgReducePerShieldPierce = resolve_num("damageReducedPerShieldPierced", obj_list)
	if dmgReducePerShieldPierce and dmgReducePerShieldPierce > 0:
		descs.append(str(dmgReducePerShieldPierce) + " " + global_labels["label_damageReducedPerShieldPierced"])
	
	# all available weapons w/ effects currently have an extra description set. 
	# inspect applyKW(Lifespan|_enemyOnly) & extraHazard(Obj|Ct) if that changes
	desc = resolve_str("description", obj_list) or ""
	desc = desc.strip("; ")
	if desc and len(desc) > 0:
		descs.append(desc)

	return "; ".join(descs)

def proc_item_summon(obj_list, entry):
	desc = entry["Description"]
	spawn_obj = None

	# Thralls are used in all kinds of ways, easier to hardcode the crew object if it shows up
	if desc.find("Thrall") > -1:
		spawn_obj = "oCrewZombie_blood"
	else:
		ability = resolve_raw("addsAbility", obj_list)
		if ability and (ability.startswith("oAblSummon") or ability.startswith("oAblConsumableSummon")):
			spawn_script = resolve_raw("applyShipEffectScript", [ability])
			if spawn_script:
				spawn_obj = spawn_crew_for_script[spawn_script]
			else:
				gml_str = read_gml(ability)
				crew_match = re.search(r'scrSpawnCrew\(\w+, \w+, (oCrew\w+)\)', gml_str, flags = re.MULTILINE)
				if crew_match:
					spawn_obj = crew_match.group(1)
		
	if spawn_obj:
		if spawn_obj not in parsed_code:
			return
		spawn_name = resolve_str("name", [spawn_obj])
		link = obj_link(spawn_obj, spawn_name)
		entry["Description"] = re.sub(spawn_name, link, desc, flags = re.IGNORECASE)

def proc_extra_item_ability_attributes(obj_list, entry):
	ability = resolve_raw("addsAbility", obj_list)
	if ability and ability.startswith("oAbl"):
		ct = resolve_num("chargeTime", [ability])
		if ct:
			entry["Charge Time"] = ct
		cd = resolve_num("cooldown", [ability])
		if cd:
			entry["Cooldown"] = cd	
		pj = proc_projectile_speed([ability])
		if pj:
			entry["Projectile Speed"] = pj

		# lots of ways duration is specified
		dr = resolve_num("applyKWLifespan", [ability])
		if not dr:
			dr = resolve_num("appliedKeywordDuration", [ability])
		if not dr:
			dr = resolve_num("duration", [ability])
		if not dr:
			effect_scr = resolve_str("applyShipEffectScript", [ability])
			if effect_scr:
				dr = extra_effect_durations.get(effect_scr)
		if dr:
			entry["Duration"] = dr

def proc_random_item(obj_list, name):
	item_obj_names = []
	choose_match = re.match(r'choose\(([^)]+)\)', name)
	
	if choose_match:
		item_obj_names = choose_match.group(1).split(", ")
	else: 
		item_obj_names = [name]
	
	item_names = []
	item_count = {}
	for obj_name in item_obj_names:
		item_name = resolve_str("name", [obj_name])
		
		if item_name:
			item_name = obj_link(obj_name, item_name)
			item_names.append(item_name)
			if item_name in item_count:
				item_count[item_name] = item_count[item_name] + 1
			else:
				item_count[item_name] = 1
	
	item_names = []
	for item_name in item_count:
		if item_count[item_name] > 1:
			item_name = f"{item_name}[{item_count[item_name]}]"
		item_names.append(item_name)
	display_name = ", ".join(item_names)
	if choose_match and len(item_names) > 1:
		display_name = f"random({display_name})"

	return display_name

def proc_resistance_value(effect_name, value_str):
	if effect_name.endswith("Resistance"):
		return min(100, int(value_str) * -1)
	return value_str

################################################################################
## MARK: Parsing & Patching
################################################################################

def resolve_num(var_name, obj_names, index = -1):
	if len(obj_names) == 0:
		return None
	
	ret_val = None
	if index > -1:
		var_name = var_name.split(":")[0] + ":" + str(index)	
	var_val = parsed_code[obj_names[0]].get(var_name, None)
	
	if var_val:
		var_val = var_val.replace("room_speed", "").replace("*", "").replace("/", "").strip()
		if re.match(r'^-?\d+\.\d+$', var_val):
			ret_val = float(var_val)
		elif re.match(r'^-?\d+$', var_val):
			ret_val = int(var_val)
		else:
			ret_val = resolve_num(var_val, obj_names)
	else:
		ret_val = resolve_num(var_name, obj_names[1:], index)
	
	return ret_val

def resolve_bool(var_name, obj_names, index = -1):
	if len(obj_names) == 0:
		return None
	
	ret_val = None
	if index > -1:
		var_name = var_name.split(":")[0] + ":" + str(index)
	var_val = parsed_code[obj_names[0]].get(var_name, None)
	if index > -1 and isinstance(var_val, list):
		var_val = var_val[index]	
	
	if var_val != None:
		if var_val == "true" or var_val == "1":
			ret_val = True
		elif var_val == "false" or var_val == "1":
			ret_val = False
		else:
			ret_val = resolve_bool(var_val, obj_names)
	else:
		ret_val = resolve_bool(var_name, obj_names[1:], index)
	
	return ret_val

def resolve_str(var_name, obj_names, index = -1):
	if len(obj_names) == 0:
		return None
	
	ret_val = None
	if index > -1:
		var_name = var_name.split(":")[0] + ":" + str(index)
	var_val = parsed_code[obj_names[0]].get(var_name, None)

	if var_val:
		parts = var_val.split(" + ")

		ret = []
		for p in parts:
			add_val = None
			
			# is string literal?
			if p[0] == '"' and p[-1] == '"':
				add_val = p[1:-1]
			# or number?
			elif re.match(r'^-?\d+(\.\d+)$', p):
				add_val = p
			# function call?
			else:
				var_name = p
				str_fn_match = re.match(r'^string\((.+)\)$', p)
				if str_fn_match:
					val = str_fn_match.group(1)
					str_format_match = re.match(r'^"([^"]+)", (\w+)$', val)
					# string("format {0}", arg) supports more than 1 arg, we don't
					if str_format_match:
						str_arg = str_format_match.group(2)
						arg_val = resolve_num(str_arg, obj_names)
						if arg_val == None:
							arg_val = resolve_str(str_arg, obj_names)
						add_val = str_format_match.group(1).replace("{0}", str(arg_val))
					else:
						int_val = resolve_num(val, obj_names)
						if int_val:
							add_val = str(int_val)
				# unknown fn call in value. preserve value as is
				elif re.match(r'^\w+\((.+)\)$', p):
					add_val = p
				else:
					add_val = resolve_str(var_name, obj_names)
					
			if add_val is None:
				# return symbol name if it cannot be resolved
				add_val = p
			else:
				add_val = add_val.removesuffix("\\n").replace("\\\"", "\"").replace("\\n\\n", "; ").replace("\\n", "; ")
			ret.append(add_val)
		ret_val = "".join(ret)
	else:
		ret_val = resolve_str(var_name, obj_names[1:], index)
	
	return ret_val

def resolve_raw(var_name, obj_names, index = -1):
	for obj_name in obj_names:
		if index > -1:
			var_name = var_name.split(":")[0] + ":" + str(index)
		var_val = parsed_code[obj_name].get(var_name, None)
		if var_val:
			return var_val
	return None

def parse_object_code():
	for obj_name in exp_data["objParentMap"]:
		code_str = read_gml(obj_name)
		if code_str:
			parsed_code[obj_name] = parse_gml(code_str)
		else:
			parsed_code[obj_name] = {}

def parse_gml(gml):
	# Only captures (indexed) assignments at root scope, generic function calls 
	# only if they aren't the value of an assignment. Indented code is ignored.
	tbl = {}
	calls = []
	
	for line in gml.splitlines():
		if line == "return":
			break
		
		line = line.removeprefix("var ")
		line = line.removesuffix(";")

		# ignore indented code
		if line.startswith(" "):
			continue
		
		# function call
		fn_match = re.search(r'^([\w_]+)\((.+)\)$', line)
		if fn_match != None:
			fn_name = fn_match.group(1)
			fn_args_str = fn_match.group(2)
			
			# preserve nested call args as string, e.g. foo(1, bar(2, 3))
			# this much simpler one liner would mess that up:
			# fn_args = list(map(str.strip, fn_match.group(2).split(",")))
			fn_args = []
			cur_i = 0
			nested_fn = False
			for i in range(len(fn_args_str)):
				if not nested_fn and fn_args_str[i] == "(":
					nested_fn = True
				if nested_fn and fn_args_str[i] == ")":
					nested_fn = False
				if not nested_fn and fn_args_str[i] == ",":
					fn_args.append(fn_args_str[cur_i:i].strip())
					cur_i = i + 1
				elif i == len(fn_args_str) - 1:
					fn_args.append(fn_args_str[cur_i:].strip())

			call = {"fn": fn_name, "args": fn_args}
			calls.append(call)
			continue
		
		# assignment
		kv_pair = kv = line.split(" = ", maxsplit = 1)
		if len(kv_pair) == 2:
			var = kv_pair[0]
			val = kv_pair[1]
			
			# simple assignment or indexed
			idx_match = re.search(r'^([\w_]+)\[(\d+)\]$', var)
			if idx_match == None:
				tbl[var] = val
			else:
				var = idx_match.group(1)
				idx = int(idx_match.group(2))
				
				tbl[var + ":" + str(idx)] = val

	tbl["__calls"] = calls
	
	return tbl

def get_game_version():
	globals_gml = read_gml("scrGlobalVars")
	version_match = re.search(r'manualVersionNumber = "([^"]+)"', globals_gml)
	if version_match:
		return version_match.group(1)
	return "UNKNOWN VERSION"

def get_global_labels():
	labels = {}
	labels_gml = read_gml("scrLocalization")
	matches = re.findall(r'localization_functionText_add\("([^"]+)", "([^"]+)"\)', labels_gml)
	for match in matches:
		labels[match[0]] = match[1]
	return labels

def get_global_vars():
	vars = {}
	vars_gml = read_gml("scrGlobalVars")
	matches = re.findall(r'global\.(\w+) = (.+);', vars_gml)
	for match in matches:
		vars[match[0]] = match[1]
	return vars

def get_extra_effect_durations():
	tbl = {}
	files = {
		"scrProjEFSystemBuff": r'system_add_effect\(\w+, \w+, (\d+)\)',
		"scrProjEFMindControl": r'crew_add_keyword\(\w+, \w+, (\d+), \d+, \d+\)',
	}
	for file, regex in files.items():
		matches = matches_in_functions(file, regex)
		matches = {fn: int(match.group(1)) for fn, match in matches.items()}
		tbl.update(matches)
	return tbl

def get_spawn_scripts():
	tbl = {}
	files = ["scrProjEFSpawnDemon", "scrProjEFSpawnZombie"]
	crew_name_regex = r'scrSpawnCrew\(\w+, \w+, (oCrew\w+)\)'
	for file in files:
		matches = matches_in_functions(file, crew_name_regex)
		matches = {fn: match.group(1) for fn, match in matches.items()}
		tbl.update(matches)
	return tbl

def patch_object_code():
	for obj_name in parsed_code:
		obj_list = hierarchy_for_object(obj_name)
		if obj_list[-1] == "oWeapon":
			patch_ship_weapon(parsed_code[obj_name], obj_name)
		elif obj_list[-1] == "oKeyword":
			patch_keyword(parsed_code[obj_name], obj_name)
		elif obj_list[-1] == "oEffect":
			patch_effect(parsed_code[obj_name], obj_name)
		elif obj_list[-1] == "oCrew":
			patch_crew(parsed_code[obj_name], obj_name)
		elif obj_list[-1] == "oItem":
			patch_item(parsed_code[obj_name], obj_name)

def patch_item(tbl, name):
	# fix inconsistent spelling/naming to make crew links work
	desc = tbl.get("description")
	if desc:
		desc = desc.replace("Bloatmite", "Bloat Mite")
		desc = desc.replace("an enchanted sword", "a Wraithblade")
		tbl["description"] = desc

def patch_crew(tbl, name):
	if "baseName" in tbl:
		tbl["name"] = tbl["baseName"]

def patch_ship_weapon(tbl, name):
	desc = tbl.get("description")
	if desc:
		desc = re.sub(r'global\.(\w+)', lambda m: f'"{global_labels[m.group(1)]}"', desc)
		desc = re.sub(r'generate_weapon_description\(\w+, \d+\)', "", desc)
		desc = re.sub(r'generate_weapon_description_ext\(\w+, \d+\, (.+)\)', lambda m: m.group(1), desc)
		desc = desc.removeprefix(" + ")
		tbl["description"] = desc
	
	if name == "oWPLanceBreach": 
		tbl["applyKWLifespan"] = global_vars["defaultAttackKeywordLifespan"]

def patch_keyword(tbl, name):
	if name.startswith("oKWFaction_"):
		tbl["description"] = "\"" + resolve_str("name", [name]) + " Faction\""
	elif name == "oKWMindControlled":
		tbl["__calls"].append({ "fn": "effect_add", "args": ["oEFMindControlled", "0"] })
	elif "description" not in tbl and "str_label" in tbl:
		tbl["description"] = tbl["str_label"]

def patch_effect(tbl, name):
	if name == "oEFAttackKW":
		tbl["description"] = parsed_code["oEFAttackKW"]["str_atkSlow"]
		return
	
	static_desc_overrides = {
		"oEFAddAbility": "Internal",
		"oEFLabel": "Internal",

		"oEFAttack": "+/- DPS",
		"oEFLife": "+/- HP",
		"oEFBonusPsiOnNearbySystem": "+/- Energy on nearby systems",

		"oEFAttackMult": "Attack multiplier",
		"oEFSpeedMult": "Speed multiplier",
		"oEFUnstable": "Expires after a given time",
	}

	# manually parse value from gml str because the vars are local in nested scope
	parse_from_gml_str_var = {
		"oEFDoorDamage": "str_doorBreakNegative",
		"oEFRepairSpeed": "str_repairSpeedNegative",
		"oEFSpeed": "str_moveSpeedNegative",
		"oEFSystemDamage": "str_sysDmgNegative",

		"oEFKillsExplode": "str",
		"oEFLifeOnKill": "str",
		"oEFPoisonAttack": "str",
	}

	if name in static_desc_overrides:
		tbl["description"] = '"' + static_desc_overrides[name] + '"'
	elif name in parse_from_gml_str_var:
		gml_str = read_gml(name)
		regex = parse_from_gml_str_var[name] + r' = (".+")'
		match = re.search(regex, gml_str, flags = re.MULTILINE)
		if match:
			tbl["description"] = match.group(1)
	elif name.endswith("Resistance"):
		tbl["description"] = '"' + name[len("oEF"):-len("Resistance")] + ' resistance [value]%"'

################################################################################
## MARK: General Utils
################################################################################
	
def read_gml(file):
	path = Path(os.path.join(export_dir, file + ".gml"))
	if os.path.exists(path):
		return path.read_text()
	return None

def hierarchy_for_object(obj_name):
	obj_list = [obj_name]
	while True:
		obj_name = exp_data["objParentMap"][obj_name]
		if obj_name and obj_name != "__NONE__" and obj_name != "oSaveObject":
			obj_list.append(obj_name)
		else:
			break
	return obj_list

def args_for_calls(obj_name, call_name):
	ret_val = []
	for call in parsed_code[obj_name]["__calls"]:
		if call["fn"] == call_name:
			ret_val.append(call["args"])
	return ret_val

def matches_in_functions(file, regex, fn_names = None):
	# a common pattern in the game code is a list of functions
	# with one key line per function we want to extract
	ret = {}

	script_gml = read_gml(file)
	cur_fn_name = None
	fn_name_regex = r'^function (\w+)'

	for line in script_gml.splitlines():
		if cur_fn_name == None:
			fn_name_match = re.search(fn_name_regex, line)
			if fn_name_match and (fn_names == None or fn_name_match.group(1) in fn_names):
				cur_fn_name = fn_name_match.group(1)
		else:
			match = re.search(regex, line)
			if match:
				ret[cur_fn_name] = match
				cur_fn_name = None

	return ret

def base64_file(filename):
	with open(filename, "rb") as file:
		return base64.b64encode(file.read()).decode()
	return None

def obj_link(obj_name, text = None):
	if text == None:
		lookup_name = ("o" if obj_name[0] != "o" else "") + obj_name
		text = resolve_str("name", hierarchy_for_object(lookup_name))
	return "{" + obj_name.lstrip("o") + "#" + text + "}"

################################################################################
## MARK: Render Output
################################################################################

def render_obj_link(val):
	return re.sub(r'{(\w+)#([^}]+)}', r'<a href="#\1" class="obj_link">\2</a>', str(val))

def render_table(data, config, name, is_static):
	# col_name: is_numeric
	col_data = { "Name": False, "InternalName": False, "ObjTags": False }
	col_float_width = {}

	# check if all data in a column is numeric and should be right aligned
	for row in data:
		for col_name in row:
			if col_name.startswith("__"):
				continue
			val = row.get(col_name, None)
			
			col_is_numeric = col_data.get(col_name, True)
			val_is_numeric = val == None or isinstance(val, int) or isinstance(val, float)

			if isinstance(val, float):
				if col_name not in col_float_width:
					col_float_width[col_name] = len(str(val).split(".")[1])
				else:
					col_float_width[col_name] = max(len(str(val).split(".")[1]), col_float_width[col_name])

			col_data[col_name] = col_is_numeric and val_is_numeric
	
	if is_static:
		del col_data["ObjTags"]
	col_order = [c for c in col_data.keys()]

	# handle grouped columns, reorder columns if necessary
	span_label_for_prefix = {}
	span_num_for_prefix = {}
	has_groups = False

	if "col_span" in config:
		for label in config["col_span"]:
			prefix = config["col_span"][label]
			span_label_for_prefix[prefix] = label
			span_num_for_prefix[prefix] = 0

			first_idx = -1
			insert_idx = -1

			for i in range(len(col_order)):
				col_name = col_order[i]
				prefix_name_pair = col_name.split(":")
				if prefix_name_pair[0] == prefix:
					has_groups = True
					span_num_for_prefix[prefix] = span_num_for_prefix[prefix] + 1

					if first_idx < 0:
						first_idx = i
						insert_idx = i + 1
					else:
						del col_order[i]
						col_order.insert(insert_idx, col_name)
						insert_idx = insert_idx + 1

	th_span_cells = []
	handled_spans = []
	th_cells = []

	cell_classes = {
		"InternalName": "col_internal_name",
		"ObjTags": "col_obj_tags",
		"Buy Price": "col_price",
	}

	# render table header cells
	for col_name in col_order:
		orig_col_name = col_name

		th_class = cell_classes.get(col_name, "")

		# colspan th
		if has_groups:
			prefix_name_pair = col_name.split(":")
			if len(prefix_name_pair) == 2:
				prefix, col_name = prefix_name_pair
				if prefix not in handled_spans:
					th_span_cells.append(f'<th class="no-sort group_name" colspan="{span_num_for_prefix[prefix]}">{span_label_for_prefix[prefix]}</th>')
					handled_spans.append(prefix)
			else:
				th_span_cells.append(f'<th class="no-sort {th_class}"></th>')
		
		# normal th
		if col_data[orig_col_name]: # is_numeric
			th_class += " numeric_val indicator-left"

		th_cells.append(f'<th title="{col_name}" class="{th_class}">{col_title_abbreviations.get(col_name, col_name)}</th>')

	thead_content = ""
	if len(th_span_cells) > 0:
		thead_content += f'\n<tr class="span_groups">{"\n\t" + "\n\t".join(th_span_cells)}</tr>'
	thead_content += f'\n<tr>{"\n\t" + "\n\t".join(th_cells)}</tr>'
	
	# render data cells, apply numeric class
	rows = []
	for entry in data:
		td_cells = []
		for col_name in col_order:
			attr_str = ""
			val = entry.get(col_name)
			td_class = cell_classes.get(col_name, "")

			if col_name == "Name":
				attr_str = f' title="{entry["InternalName"]}"'
				val = f'<a href="#{entry["InternalName"]}" class="obj_link">{val}</a>'			
			if col_data[col_name]: # is_numeric
				td_class += " numeric_val"
				if val == None:
					val = 0
				if col_name in col_float_width:
					val = f"%.{col_float_width[col_name]}f" % val
			
			attr_str += ' class="' + td_class + '"'
			
			if isinstance(val, list):
				val = list(map(lambda s: re.sub(r'<(\w+)>', "[\\1]", str(s)), val))
				val = "<br>".join(val)
			else:
				val = re.sub(r'<(\w+)>', "[\\1]", str(val))
			
			val = render_obj_link(val)

			if val == "True":
				val = "&#x2713;"
			elif val == "False":
				val = "&#x2A09;"

			td_cells.append(f'<td{attr_str}>{val}</td>')
		
		tr_class = ""
		if entry.get("__unavailable"):
			tr_class = "unavailable"
		rows.append(f'<tr id="{entry["InternalName"]}" class="{tr_class}">{"\n\t" + "\n\t".join(td_cells)}</tr>')
	tbody_content = "\n".join(rows)
		
	return f'\n\n<table id="data_{name}" class="sortable {"grouped_header" if has_groups else ""}">\n<thead>{thead_content}</thead>\n<tbody>{tbody_content}</tbody>\n</table>'

def render_html(proc_data, config, game_version, ref_version, fraktur_font, gh_icon):
	template = template_path.read_text()
	
	nav_html = []
	data_html = ""
	for k in cat_config:
		group_cfg = config[k].get("group")
		is_static = config[k]["fn"] == None

		data_html += f"<h2 id='{k}'>{k} <a href='#top'>&#x2B71;</a></h2>"
		nav_html_item = f"<a id='nav_{k}' href='#{k}'>{k}</a>"
				
		if group_cfg:
			groups = {}
			group_key = group_cfg["key"]
			nav_html_item += ": "
			
			for entry in proc_data[k]:
				group_val = entry[group_key]
				del entry[group_key]
				
				if group_val in groups:
					groups[group_val].append(entry)
				else:
					groups[group_val] = [entry]
			
			subLinks = []
			for gk in group_cfg["order"]:
				data_html += f"<h3 id='{gk}'>{gk} <a href='#top'>&#x2B71;</a></h3>"
				subLinks.append(f"<a id='nav_{gk}' href='#{gk}'>{gk}</a>")
				
				table = render_table(groups[gk], config[k], gk, is_static)
				data_html += table
			nav_html_item += "&#8201;&#183;&#8201;".join(subLinks)
		else:
			table = render_table(proc_data[k], config[k], k, is_static)
			data_html += table
		
		nav_html.append(nav_html_item)
		
	replacements = {
		"##GAME_VERSION##": game_version,
		"##REF_VERSION##": ref_version,
		"##DATA##": data_html,
		"##NAV##": "&#8201;|&#8201;".join(nav_html),
		"##FRAKTUR_FONT##": fraktur_font,
		"##GH_ICON##": gh_icon,
	}
	for k, v in replacements.items():
		template = template.replace(k, v)
	return template

################################################################################
## MARK: Main run()
################################################################################

def run():
	exp_data.update(json.loads(data_json_path.read_text()))

	global_labels.update(get_global_labels())
	global_vars.update(get_global_vars())
	spawn_crew_for_script.update(get_spawn_scripts())
	extra_effect_durations.update(get_extra_effect_durations())

	parse_object_code()
	patch_object_code()
	proc_object_code()
	proc_static()

	game_v = get_game_version()
	ref_v = date.today().strftime("%y-%m-%d")
	# we're embedding ~70kb of cosmetic b64 data for a bit over 400kb of  
	# useful data but since we're not loading several 100kb of JS libs, 
	# tracking & ad code it should all balance out in the end. :)
	b64_font = base64_file(Path(os.path.join(base_dir, "res", "OldEnglishFive.ttf")))
	b64_gh_icon = base64_file(Path(os.path.join(base_dir, "res", "github-icon-64.png")))

	html = render_html(proc_data, cat_config, game_v, ref_v, b64_font, b64_gh_icon)
	f = open("index.html", "w")
	f.write(html)
	f.close()

run()