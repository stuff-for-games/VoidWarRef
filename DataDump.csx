using System.Text;
using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Collections;
using System.Collections.Specialized;
using System.Collections.Generic;
using System.Linq;
using System.ComponentModel;
using Underanalyzer;
using Underanalyzer.Decompiler;
using Underanalyzer.Decompiler.AST;
using System.Runtime.InteropServices;
using System.Text.Json;

EnsureDataLoaded();

if (Data.IsYYC())
{
    ScriptError("The opened game uses YYC: no code is available.");
    return;
}

// string codeFolder = "C:\\Users\\Paul\\VoidWarData";
string codeFolder = PromptChooseDirectory();
if (codeFolder is null)
{
    return;
}

public class ExpObjData
{
	public List<ObjCategory> objCatData { get; set; } = new List<ObjCategory>();
	public List<string> parents { get; set; } = new List<string>();
	public Dictionary<string, string> objParentMap { get; set; } = new Dictionary<string, string>();
	public Dictionary<string, List<string>> objTagsMap { get; set; } = new Dictionary<string, List<string>>();
}

public class ObjCategory
{
	public string name { get; set; }
	public string objPrefix { get; set; }
	public List<string> parents { get; set; }
	public List<string> objNames { get; set; }
	
	public ObjCategory(string name, string prefix, List<string> parents)
	{
		this.name = name;
		this.objPrefix = prefix;
		this.parents = parents;
		this.objNames = [];
	}
	
	public bool IncludeObject(string objName, List<string> parentList)
	{
		bool containsAll = parents.All(x => parentList.Any(y => x == y));
		if (parents.Contains(objName))
				return true;

		if (containsAll)
		{
			if (objName.StartsWith(objPrefix))
			{
				objNames.Add(objName);
				return true;
			}
		}
		
		return false;
	}
}

var expData = new ExpObjData();

// Order matters:
//	Commanders before units or the latter would include the oCrewPlayer parent
//	Consumables before Tools because they share their parents and can only be differentiated by their prefix
expData.objCatData = [
	new ObjCategory("Boss Weapons", "oSys", ["oSysBossArtillery", "oSystem", "oSysGroup"]),
	new ObjCategory("Systems", "oSys", ["oSystem", "oSysGroup"]),
	new ObjCategory("Subsystems", "oSys", ["oSubsystem", "oSysGroup"]),
	new ObjCategory("Modules", "oModule", ["oModule"]),
	new ObjCategory("Commanders", "oCrewPlayer", ["oCrewPlayer", "oCrew"]),
	new ObjCategory("Crew", "oCrew", ["oCrew"]),
	new ObjCategory("Missiles", "oWPMissile", ["oOrdnance", "oWeapon"]),
	new ObjCategory("Armaments", "oWP", ["oWeapon"]),
	new ObjCategory("Consumables", "oConsumable", ["oItemTool", "oItem"]),
	new ObjCategory("Armor", "oItemArmor", ["oItemArmor", "oItem"]),
	new ObjCategory("Psychomancies", "oItem", ["oItemPsychic", "oItem"]),
	new ObjCategory("Tools", "oItem", ["oItemTool", "oItem"]),
	new ObjCategory("Weapons", "oItem", ["oItemWeapon", "oItem"]),
	new ObjCategory("Psy Abilities", "oAbl", ["oAbilityPsychic", "oAbility"]),
	new ObjCategory("Tool Abilities", "oAbl", ["oAbilityEquipment", "oAbility"]),
	new ObjCategory("Keywords", "oKW", ["oKeyword"]),
	new ObjCategory("Effects", "oEF", ["oEffect"]),
	
	// the following categories are only used internally (e.g. get projectile speed for weapons and spells)
	new ObjCategory("Projectiles1", "o", ["oProjectileMissile", "oProjectile"]),
	new ObjCategory("Projectiles2", "o", ["oShotLance", "oProjectile"]),
	new ObjCategory("Projectiles3", "o", ["oCannonShot", "oProjectile"]),
	new ObjCategory("Projectiles4", "o", ["oAreaShot", "oProjectile"]),
	new ObjCategory("Projectiles5", "o", ["oProjectile"]),
];

GlobalDecompileContext globalDecompileContext = new(Data);
Underanalyzer.Decompiler.IDecompileSettings decompilerSettings = Data.ToolInfo.DecompilerSettings;

List<UndertaleCode> toDump = [];

await Run();

await StopProgressBarUpdater();
HideProgressBar();

async Task Run()
{
	string folder = Path.Combine(codeFolder, "gml_code");
	Directory.CreateDirectory(folder);

	PopulateExportData();
	WriteExportDataJson();
	PopulateCodeToDump();

	SetProgressBar(null, "Code Entries", 0, toDump.Count);
	StartProgressBarUpdater();
	
	await Task.Run(() => Parallel.ForEach(toDump, DumpCode));
}

void PopulateExportData()
{
	var uniqueParents = new HashSet<string>();
	
	foreach (var obj in Data.GameObjects)
	{
		List<string> parentList = [];		
		var parent = obj.ParentId;
		string parentName = null;
		while (true)
		{
			if (parent == null)
				break;

			parentList.Add(parent.Name.Content);
			parent = parent.ParentId;
		}
		if (parentList.Count > 0)
			parentName = parentList[0];

		foreach (var catInfo in expData.objCatData)
		{

			if (catInfo.IncludeObject(obj.Name.Content, parentList))
			{
				int tagID = UndertaleTags.GetAssetTagID(Data, obj);
				var tagList = new List<string>();
				if (Data.Tags.AssetTags.ContainsKey(tagID))
				{
					var tags = Data.Tags.AssetTags[tagID];
					for (int i = 0; i < tags.Count; i++)
					{
						tagList.Add(tags[i].Content);
					}
				}

				expData.objTagsMap[obj.Name.Content] = tagList;
				expData.objParentMap[obj.Name.Content] = parentName ?? "__NONE__";

				if (parentName != null)
					uniqueParents.Add(parentName);

				break;
			}
		}
	}
	
	expData.parents = uniqueParents.ToList();
	expData.parents.Sort();
}

void PopulateCodeToDump()
{
	foreach (var code in Data.Code)
		if (code.ParentEntry == null)
			toDump.Add(code);
}

void WriteExportDataJson()
{
	var options = new JsonSerializerOptions { WriteIndented = true };
	var jsonString = JsonSerializer.Serialize(expData, options);
    File.WriteAllText(Path.Combine(codeFolder, "gml_code", "data.json"), jsonString);
}

void DumpCode(UndertaleCode code)
{
    if (code is not null)
    {
		string name = code.Name.Content;
		name = name.Replace("gml_GlobalScript_", "");
		name = name.Replace("gml_Object_", "");
		name = name.Replace("_Create_0", "");
        string path = Path.Combine(codeFolder, "gml_code", name + ".gml");
        try
        {
            File.WriteAllText(path, (code != null 
                ? new Underanalyzer.Decompiler.DecompileContext(globalDecompileContext, code, decompilerSettings).DecompileToString() 
                : ""));
        }
        catch (Exception e)
        {
            File.WriteAllText(path, "/*\nDECOMPILER FAILED!\n\n" + e.ToString() + "\n*/");
        }
    }

    IncrementProgressParallel();
}
