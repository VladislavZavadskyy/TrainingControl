$(document).ready(function () {
    $('.input_form').on("submit", function () {
        newMessage($(this));
        return false;
    });

    $(document).delegate('textarea', 'keydown', function(e) {
      var keyCode = e.keyCode || e.which;

      if (keyCode == 9) {
        e.preventDefault();
        var start = this.selectionStart;
        var end = this.selectionEnd;

        // set textarea value to: text before caret + tab + text after caret
        $(this).val($(this).val().substring(0, start)
                    + "\t"
                    + $(this).val().substring(end));

        // put caret at right position again
        this.selectionStart =
        this.selectionEnd = start + 1;
      }
    });
});

function newMessage(form) {
    let message = form.formToDict();
    $.ajax({
        type: 'POST',
        url: '/',
        data: message,
        success: function (data, status, xhttp) {
            let message_container = $("#response_table")[0];
            let existing_responses = $(".response").toArray();
            let existing_response_uuids = existing_responses.map(r => r.getAttribute('_uuid'));
            for (response of data['responses']) {
                if (existing_response_uuids.includes(response['_uuid'])) continue;

                let tr = document.createElement("tr");
                let td = document.createElement("td");

                tr.appendChild(td);
                td.classList.add("response");
                td.setAttribute('_uuid', response['_uuid']);

                let date_span = document.createElement("span");
                date_span.appendChild(document.createTextNode(response['time']));
                date_span.classList.add("datetime");

                td.appendChild(date_span);
                td.appendChild(document.createTextNode(response['content']));
                message_container.appendChild(tr);

                if (response['success']) {
                    let message_keys = Object.keys(message);
                    message_keys.pop('_xsrf');
                    let form_key = message_keys[0];

                    let value_cell = $('#' + form_key + '_value');
                    if (value_cell) {
                        value_cell[0].innerText = message[form_key];
                    }
                }
            }
            return false;
        }
    });
}

jQuery.fn.formToDict = function() {
    var fields = this.serializeArray();
    var json = {};
    for (var i = 0; i < fields.length; i++) {
        json[fields[i].name] = fields[i].value;
    }
    if (json.next) delete json.next;
    return json;
};
