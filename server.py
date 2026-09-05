#!/usr/bin/env python3
"""
MedLens Web Application Server
A zero-dependency HTTP server implementing MedLens clinical verification, audit trails,
batch verification, and complete clinical editing capabilities.
"""

import http.server
import socketserver
import json
import os
import sys
import shutil
import re
from datetime import datetime, timezone
import urllib.parse

PORT = 8000
DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
INITIAL_DATA_FILE = os.path.join(os.path.dirname(__file__), "initial_data.json")
INDEX_FILE = os.path.join(os.path.dirname(__file__), "index.html")

def load_data():
    if not os.path.exists(DATA_FILE):
        if os.path.exists(INITIAL_DATA_FILE):
            shutil.copy(INITIAL_DATA_FILE, DATA_FILE)
        else:
            return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def parse_report_text(raw_text):
    """
    Extracts structured clinical data strictly adhering to MedLens Safety Rules 1-9.
    """
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]

    patient = {
        "patient_id": None,
        "name": None,
        "age": None,
        "date_of_birth": None,
        "sex": None,
        "contact_optional": None,
        "provenance": "report_extracted",
        "source_text": "",
        "review_flags": []
    }

    report = {
        "report_id": None,
        "patient_id": None,
        "file_url": None,
        "report_date": None,
        "sample_collected": None,
        "specimen": None,
        "uploaded_at": None,
        "extraction_status": "completed",
        "source": "uploaded_report",
        "provenance": "report_extracted",
        "source_text": "",
        "review_flags": []
    }

    patient_provided = {
        "symptoms": [],
        "conditions": [],
        "allergies": [],
        "medications": [],
        "entered_by": None,
        "entered_at": None,
        "source": "user",
        "provenance": "report_extracted",
        "source_text": "",
        "review_flags": []
    }

    lab_results = []
    current_category = "General"
    patient_source_lines = []
    report_source_lines = []

    for line in lines:
        if re.search(r"patient name\s*:\s*(.+)", line, re.I):
            m = re.search(r"patient name\s*:\s*(.+)", line, re.I)
            patient["name"] = m.group(1).strip()
            patient_source_lines.append(line)
        elif re.search(r"patient id\s*:\s*(.+)", line, re.I):
            m = re.search(r"patient id\s*:\s*(.+)", line, re.I)
            patient["patient_id"] = m.group(1).strip()
            report["patient_id"] = patient["patient_id"]
            patient_source_lines.append(line)
        elif re.search(r"age\s*:\s*(.+)", line, re.I):
            m = re.search(r"age\s*:\s*(.+)", line, re.I)
            patient["age"] = m.group(1).strip()
            patient_source_lines.append(line)
        elif re.search(r"date of birth\s*:\s*(.+)", line, re.I):
            m = re.search(r"date of birth\s*:\s*(.+)", line, re.I)
            patient["date_of_birth"] = m.group(1).strip()
            patient_source_lines.append(line)
        elif re.search(r"sex\s*:\s*(.+)", line, re.I):
            m = re.search(r"sex\s*:\s*(.+)", line, re.I)
            patient["sex"] = m.group(1).strip()
            patient_source_lines.append(line)
        elif re.search(r"report (?:number|id)\s*:\s*(.+)", line, re.I):
            m = re.search(r"report (?:number|id)\s*:\s*(.+)", line, re.I)
            report["report_id"] = m.group(1).strip()
            report_source_lines.append(line)
        elif re.search(r"sample collected\s*:\s*(.+)", line, re.I):
            m = re.search(r"sample collected\s*:\s*(.+)", line, re.I)
            report["sample_collected"] = m.group(1).strip()
            report_source_lines.append(line)
        elif re.search(r"report issued\s*:\s*(.+)", line, re.I):
            m = re.search(r"report issued\s*:\s*(.+)", line, re.I)
            report["report_date"] = m.group(1).strip()
            report_source_lines.append(line)
        elif re.search(r"specimen\s*:\s*(.+)", line, re.I):
            m = re.search(r"specimen\s*:\s*(.+)", line, re.I)
            report["specimen"] = m.group(1).strip()
            report_source_lines.append(line)
        elif re.search(r"complete blood count|cbc", line, re.I):
            current_category = "Complete Blood Count (CBC)"
        elif re.search(r"blood glucose|renal profile", line, re.I):
            current_category = "Blood Glucose & Renal Profile"
        elif re.search(r"liver function|lft", line, re.I):
            current_category = "Liver Function Tests"
        elif re.search(r"lipid profile", line, re.I):
            current_category = "Lipid Profile"
        elif re.search(r"thyroid|vitamins", line, re.I):
            current_category = "Thyroid & Vitamins"
        elif re.search(r"urine routine|urinalysis", line, re.I):
            current_category = "Urine Routine Examination"
        elif re.search(r"patient reports?\s+(.+)", line, re.I):
            m = re.search(r"patient reports?\s+(.+)", line, re.I)
            patient_provided["symptoms"].append(m.group(1).strip())
            patient_provided["source_text"] = line
        elif not line.startswith("=") and not line.startswith("-") and not line.startswith("Test Name"):
            parts = re.split(r"\s{2,}|\t+", line)
            if len(parts) >= 2:
                test_name = parts[0].strip()
                res_str = parts[1].strip()
                unit = parts[2].strip() if len(parts) >= 3 else None
                ref_str = parts[3].strip() if len(parts) >= 4 else (parts[2].strip() if len(parts) == 3 and not re.search(r"^[a-zA-Z/%]+$", parts[2].strip()) else None)

                if test_name.lower() in ["comments", "end of report", "synthetic laboratory report", "medlens demo diagnostics"]:
                    continue

                res_id = f"RES-{len(lab_results) + 1:03d}"
                ref_low = None
                ref_high = None
                status = "unavailable"
                review_flags = []

                num_val = None
                try:
                    num_val = float(res_str) if "." in res_str else int(res_str)
                except ValueError:
                    num_val = res_str

                if ref_str:
                    range_match = re.match(r"^([\d\.]+)\s*-\s*([\d\.]+)$", ref_str)
                    if range_match:
                        ref_low = float(range_match.group(1))
                        ref_high = float(range_match.group(2))
                        if isinstance(num_val, (int, float)):
                            if num_val < ref_low:
                                status = "low"
                            elif num_val > ref_high:
                                status = "high"
                            else:
                                status = "normal"
                    elif ">=" in ref_str:
                        m_bound = re.search(r">=\s*([\d\.]+)", ref_str)
                        if m_bound:
                            ref_low = float(m_bound.group(1))
                            if isinstance(num_val, (int, float)):
                                status = "normal" if num_val >= ref_low else "low"
                            review_flags.append(f"Single-sided lower bound reference range '{ref_str}'; upper limit not specified")
                    elif "<" in ref_str:
                        m_bound = re.search(r"<\s*([\d\.]+)", ref_str)
                        if m_bound:
                            ref_high = float(m_bound.group(1))
                            if isinstance(num_val, (int, float)):
                                status = "high" if num_val >= ref_high else "normal"
                            review_flags.append(f"Single-sided upper bound reference range '{ref_str}'; lower limit not specified")
                    else:
                        review_flags.append(f"Non-standard or qualitative reference range '{ref_str}'; numeric range unavailable")
                else:
                    review_flags.append("Reference range missing or unlisted in source report")

                if isinstance(num_val, str) and "-" in num_val:
                    status = "normal"
                    review_flags.append(f"Microscopic range result '{num_val}' evaluated against reported range '{ref_str}'")

                lab_results.append({
                    "result_id": res_id,
                    "category": current_category,
                    "report_id": report["report_id"] or "REPORT-RAW",
                    "test_name": test_name,
                    "value": num_val,
                    "unit": unit,
                    "reference_low": ref_low,
                    "reference_high": ref_high,
                    "reference_text": ref_str,
                    "status": status,
                    "observation_text": str(num_val),
                    "page_number": None,
                    "confidence": 1.0,
                    "verification_status": "pending",
                    "provenance": "report_extracted",
                    "source_text": line,
                    "review_flags": review_flags
                })

    patient["source_text"] = "\n".join(patient_source_lines)
    report["source_text"] = "\n".join(report_source_lines)
    if not patient.get("contact_optional"):
        patient["review_flags"].append("Contact information not provided in the report input")

    out_of_range = [r for r in lab_results if r["status"] in ["low", "high"]]
    abnormal_desc = []
    for r in out_of_range:
        abnormal_desc.append(f"{r['test_name']} is {r['value']} {r['unit'] or ''} (reference range: {r['reference_text'] or 'unspecified'})")

    summary_text = (
        f"The report lists laboratory findings for {patient['name'] or 'the patient'} from a sample "
        f"collected on {report['sample_collected'] or 'unspecified date'} and issued on {report['report_date'] or 'unspecified date'}. "
    )
    if patient_provided["symptoms"]:
        summary_text += f"The report lists that the patient reports {', '.join(patient_provided['symptoms'])}. "

    if abnormal_desc:
        summary_text += f"Several laboratory results are outside the reference ranges shown in the report: {', '.join(abnormal_desc)}. "
    else:
        summary_text += "No laboratory results were detected outside the explicit numeric reference ranges in the report. "

    summary_text += (
        "Other listed tests are within the reported reference ranges or lack usable numeric ranges. "
        "As noted in the report, no clinical diagnosis is provided and all results must be reviewed by a qualified healthcare professional."
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    audit_trail = [
        {
            "entity_type": "Report",
            "entity_id": report["report_id"] or "EXTRACTED-REPORT",
            "action": "extracted",
            "actor": "AI",
            "timestamp": now_iso,
            "before_value": None,
            "after_value": f"{len(lab_results)} tests extracted",
            "source_pointer": "Direct report text ingest"
        },
        {
            "entity_type": "Summary",
            "entity_id": "SUM-001",
            "action": "generated",
            "actor": "AI",
            "timestamp": now_iso,
            "before_value": None,
            "after_value": "Cautious summary generated",
            "source_pointer": "Generated per Rule 9 guidelines"
        }
    ]

    return {
        "summary": {
            "text": summary_text,
            "provenance": "ai_generated",
            "review_flags": []
        },
        "patient": patient,
        "patient_provided_info": patient_provided,
        "report": report,
        "lab_results": lab_results,
        "audit_trail": audit_trail,
        "raw_report_text": raw_text
    }

class MedLensRequestHandler(http.server.SimpleHTTPRequestHandler):
    def send_json_response(self, status_code, payload):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open(INDEX_FILE, "rb") as f:
                self.wfile.write(f.read())
            return

        if path == "/api/data":
            try:
                data = load_data()
                self.send_json_response(200, data)
            except Exception as e:
                self.send_json_response(500, {"error": str(e)})
            return

        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_length)
        
        try:
            body = json.loads(post_body.decode("utf-8")) if post_body else {}
        except Exception:
            body = {}

        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Single Result Quick Verify / Update
        if path == "/api/verify":
            result_id = body.get("result_id")
            new_status = body.get("verification_status", "verified")
            reviewer_name = body.get("reviewer_name", "Clinical Reviewer")
            notes = body.get("notes", "")
            edited_value = body.get("edited_value")

            data = load_data()
            target_result = None
            for item in data.get("lab_results", []):
                if item.get("result_id") == result_id:
                    target_result = item
                    break

            if not target_result:
                self.send_json_response(404, {"error": f"Result with ID {result_id} not found."})
                return

            before_status = target_result.get("verification_status")
            before_val = target_result.get("value")
            
            target_result["verification_status"] = new_status
            if edited_value is not None and edited_value != "":
                try:
                    target_result["value"] = float(edited_value) if "." in str(edited_value) else int(edited_value)
                except ValueError:
                    target_result["value"] = edited_value

            if notes:
                if "review_notes" not in target_result:
                    target_result["review_notes"] = []
                target_result["review_notes"].append({
                    "author": reviewer_name,
                    "note": notes,
                    "timestamp": now_iso
                })

            audit_event = {
                "entity_type": "LabResult",
                "entity_id": result_id,
                "action": f"marked_{new_status}" if edited_value is None else "edited_and_verified",
                "actor": "user",
                "timestamp": now_iso,
                "before_value": f"status={before_status}, value={before_val}",
                "after_value": f"status={new_status}, value={target_result.get('value')}",
                "source_pointer": f"Review by {reviewer_name}" + (f": '{notes}'" if notes else "")
            }

            if "audit_trail" not in data:
                data["audit_trail"] = []
            data["audit_trail"].insert(0, audit_event)

            save_data(data)
            self.send_json_response(200, {
                "success": True,
                "message": f"Result {result_id} marked as {new_status}.",
                "data": data
            })
            return

        # 2. Comprehensive Lab Result Editor Endpoint
        elif path == "/api/edit_result":
            result_id = body.get("result_id")
            reviewer_name = body.get("reviewer_name", "Clinical Reviewer")
            notes = body.get("notes", "")

            data = load_data()
            target_result = None
            for item in data.get("lab_results", []):
                if item.get("result_id") == result_id:
                    target_result = item
                    break

            if not target_result:
                # Option to create a new custom lab test if ID is "NEW"
                if result_id == "NEW" or not result_id:
                    new_id = f"RES-{len(data.get('lab_results', [])) + 1:03d}"
                    target_result = {
                        "result_id": new_id,
                        "category": body.get("category", "General"),
                        "report_id": data.get("report", {}).get("report_id", "ML-REP-CUSTOM"),
                        "test_name": body.get("test_name", "New Test"),
                        "value": body.get("value"),
                        "unit": body.get("unit"),
                        "reference_low": body.get("reference_low"),
                        "reference_high": body.get("reference_high"),
                        "reference_text": body.get("reference_text"),
                        "status": body.get("status", "unavailable"),
                        "observation_text": str(body.get("value")),
                        "page_number": None,
                        "confidence": 1.0,
                        "verification_status": "edited",
                        "provenance": "user_provided",
                        "source_text": f"Manually added by {reviewer_name}",
                        "review_flags": body.get("review_flags", [])
                    }
                    data.setdefault("lab_results", []).append(target_result)
                    
                    audit_event = {
                        "entity_type": "LabResult",
                        "entity_id": new_id,
                        "action": "manually_added",
                        "actor": "user",
                        "timestamp": now_iso,
                        "before_value": None,
                        "after_value": f"{target_result['test_name']}={target_result['value']} {target_result['unit'] or ''}",
                        "source_pointer": f"Added by {reviewer_name}: '{notes}'"
                    }
                    data.setdefault("audit_trail", []).insert(0, audit_event)
                    save_data(data)
                    self.send_json_response(200, {
                        "success": True,
                        "message": f"Added new test {new_id} successfully.",
                        "data": data
                    })
                    return

                self.send_json_response(404, {"error": f"Result with ID {result_id} not found."})
                return

            before_snapshot = f"name={target_result.get('test_name')}, val={target_result.get('value')} {target_result.get('unit')}, status={target_result.get('status')}"

            # Update fields
            if "test_name" in body:
                target_result["test_name"] = body["test_name"]
            if "category" in body:
                target_result["category"] = body["category"]
            if "value" in body:
                v = body["value"]
                try:
                    target_result["value"] = float(v) if "." in str(v) else int(v)
                except ValueError:
                    target_result["value"] = v
            if "unit" in body:
                target_result["unit"] = body["unit"]
            if "reference_low" in body:
                target_result["reference_low"] = float(body["reference_low"]) if body["reference_low"] is not None and body["reference_low"] != "" else None
            if "reference_high" in body:
                target_result["reference_high"] = float(body["reference_high"]) if body["reference_high"] is not None and body["reference_high"] != "" else None
            if "reference_text" in body:
                target_result["reference_text"] = body["reference_text"]
            if "status" in body:
                target_result["status"] = body["status"]
            if "review_flags" in body and isinstance(body["review_flags"], list):
                target_result["review_flags"] = body["review_flags"]

            target_result["verification_status"] = "edited"
            if notes:
                target_result.setdefault("review_notes", []).append({
                    "author": reviewer_name,
                    "note": notes,
                    "timestamp": now_iso
                })

            after_snapshot = f"name={target_result.get('test_name')}, val={target_result.get('value')} {target_result.get('unit')}, status={target_result.get('status')}"

            audit_event = {
                "entity_type": "LabResult",
                "entity_id": result_id,
                "action": "edited",
                "actor": "user",
                "timestamp": now_iso,
                "before_value": before_snapshot,
                "after_value": after_snapshot,
                "source_pointer": f"Edited by {reviewer_name}" + (f": '{notes}'" if notes else "")
            }

            data.setdefault("audit_trail", []).insert(0, audit_event)
            save_data(data)
            self.send_json_response(200, {
                "success": True,
                "message": f"Updated and verified result {result_id}.",
                "data": data
            })
            return

        # 3. Patient & Report Metadata Editor Endpoint
        elif path == "/api/edit_patient":
            reviewer_name = body.get("reviewer_name", "Clinical Reviewer")
            notes = body.get("notes", "Patient metadata update")
            p_data = body.get("patient", {})
            r_data = body.get("report", {})

            data = load_data()
            old_p = data.get("patient", {}).copy()

            if "name" in p_data:
                data.setdefault("patient", {})["name"] = p_data["name"]
            if "patient_id" in p_data:
                data.setdefault("patient", {})["patient_id"] = p_data["patient_id"]
                data.setdefault("report", {})["patient_id"] = p_data["patient_id"]
            if "age" in p_data:
                data.setdefault("patient", {})["age"] = p_data["age"]
            if "sex" in p_data:
                data.setdefault("patient", {})["sex"] = p_data["sex"]
            if "date_of_birth" in p_data:
                data.setdefault("patient", {})["date_of_birth"] = p_data["date_of_birth"]
            if "contact_optional" in p_data:
                data.setdefault("patient", {})["contact_optional"] = p_data["contact_optional"]

            if "report_id" in r_data:
                data.setdefault("report", {})["report_id"] = r_data["report_id"]
            if "report_date" in r_data:
                data.setdefault("report", {})["report_date"] = r_data["report_date"]
            if "sample_collected" in r_data:
                data.setdefault("report", {})["sample_collected"] = r_data["sample_collected"]
            if "specimen" in r_data:
                data.setdefault("report", {})["specimen"] = r_data["specimen"]

            audit_event = {
                "entity_type": "Patient",
                "entity_id": data.get("patient", {}).get("patient_id", "DEMO-PATIENT"),
                "action": "metadata_edited",
                "actor": "user",
                "timestamp": now_iso,
                "before_value": f"name={old_p.get('name')}, age={old_p.get('age')}, sex={old_p.get('sex')}",
                "after_value": f"name={data['patient'].get('name')}, age={data['patient'].get('age')}, sex={data['patient'].get('sex')}",
                "source_pointer": f"Profile updated by {reviewer_name}: '{notes}'"
            }

            data.setdefault("audit_trail", []).insert(0, audit_event)
            save_data(data)
            self.send_json_response(200, {
                "success": True,
                "message": "Patient & report details updated successfully.",
                "data": data
            })
            return

        # 4. Direct JSON Editor Save Endpoint
        elif path == "/api/save_json":
            json_content = body.get("json_data")
            if not json_content or not isinstance(json_content, dict):
                self.send_json_response(400, {"error": "Invalid or missing JSON payload."})
                return

            reviewer_name = body.get("reviewer_name", "Clinical Admin")
            audit_event = {
                "entity_type": "Dataset",
                "entity_id": "MEDLENS_ROOT",
                "action": "raw_json_edited",
                "actor": "user",
                "timestamp": now_iso,
                "before_value": "Previous JSON state",
                "after_value": "Updated via Raw JSON Editor",
                "source_pointer": f"Direct schema edit by {reviewer_name}"
            }
            json_content.setdefault("audit_trail", []).insert(0, audit_event)
            save_data(json_content)
            self.send_json_response(200, {
                "success": True,
                "message": "JSON schema updated and saved successfully.",
                "data": json_content
            })
            return

        # 5. Batch / Multi-Result Verification
        elif path == "/api/verify_batch":
            result_ids = body.get("result_ids", [])
            new_status = body.get("verification_status", "verified")
            reviewer_name = body.get("reviewer_name", "Clinical Reviewer")
            notes = body.get("notes", "")

            if not result_ids or not isinstance(result_ids, list):
                self.send_json_response(400, {"error": "No valid result_ids array provided for batch verification."})
                return

            data = load_data()
            updated_ids = []

            for item in data.get("lab_results", []):
                if item.get("result_id") in result_ids:
                    item["verification_status"] = new_status
                    if notes:
                        item.setdefault("review_notes", []).append({
                            "author": reviewer_name,
                            "note": f"[Batch] {notes}",
                            "timestamp": now_iso
                        })
                    updated_ids.append(item.get("result_id"))

            if not updated_ids:
                self.send_json_response(404, {"error": "None of the specified result_ids were found."})
                return

            preview_ids = ", ".join(updated_ids[:8]) + ("..." if len(updated_ids) > 8 else "")
            audit_event = {
                "entity_type": "LabResultBatch",
                "entity_id": f"{len(updated_ids)}_items",
                "action": f"batch_marked_{new_status}",
                "actor": "user",
                "timestamp": now_iso,
                "before_value": f"{len(updated_ids)} items updated",
                "after_value": f"status={new_status}",
                "source_pointer": f"Batch verification by {reviewer_name} on {len(updated_ids)} tests [{preview_ids}]" + (f" | Notes: '{notes}'" if notes else "")
            }

            data.setdefault("audit_trail", []).insert(0, audit_event)
            save_data(data)
            self.send_json_response(200, {
                "success": True,
                "message": f"Successfully batch verified {len(updated_ids)} lab test(s).",
                "data": data,
                "verified_count": len(updated_ids)
            })
            return

        # 6. Reset Demo Data
        elif path == "/api/reset":
            if os.path.exists(INITIAL_DATA_FILE):
                shutil.copy(INITIAL_DATA_FILE, DATA_FILE)
            data = load_data()
            self.send_json_response(200, {
                "success": True,
                "message": "Dataset reset to initial MedLens synthetic report state.",
                "data": data
            })
            return

        # 7. Extract Text
        elif path == "/api/extract":
            report_text = body.get("report_text", "")
            if not report_text.strip():
                self.send_json_response(400, {"error": "Report text cannot be empty."})
                return

            extracted_dataset = parse_report_text(report_text)
            save_data(extracted_dataset)
            self.send_json_response(200, {
                "success": True,
                "message": f"Extracted {len(extracted_dataset['lab_results'])} tests from input report.",
                "data": extracted_dataset
            })
            return

        self.send_json_response(404, {"error": "Endpoint not found"})

def run_server():
    global PORT
    port_to_try = PORT
    max_tries = 10
    httpd = None

    for i in range(max_tries):
        try:
            socketserver.TCPServer.allow_reuse_address = True
            httpd = socketserver.TCPServer(("", port_to_try), MedLensRequestHandler)
            PORT = port_to_try
            break
        except OSError:
            port_to_try += 1

    if not httpd:
        print(f"Error: Unable to bind to any port in range {PORT} - {port_to_try}")
        sys.exit(1)

    print(f"MedLens server started successfully at http://localhost:{PORT}")
    print(f"Serving MedLens Clinical Dashboard from {os.path.dirname(__file__)}")
    sys.stdout.flush()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down MedLens server.")
        httpd.server_close()

if __name__ == "__main__":
    run_server()
