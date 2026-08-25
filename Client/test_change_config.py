from orchestrator import Orchestrator
import time


orchestrator = Orchestrator()
orchestrator.start_all()
orchestrator.change_config("sniffer", "method", "regex")
time.sleep(5)
orchestrator.change_config("error_detection", "enabled", "True")
time.sleep(5)
orchestrator.change_config("keystroke_svm", "enabled", "True")
time.sleep(5)
orchestrator.change_config("keylogger", "enabled", "True")
time.sleep(5)
orchestrator.change_config("paperclip", "enabled", "True")
time.sleep(5)
orchestrator.change_config("program_monitor", "enabled", "True")
time.sleep(5)

time.sleep(20)
orchestrator.combine_logs()
time.sleep(5)
orchestrator.stop_all()
